"""Read-only cloud acquisition. rmapi pairs/lists; Python downloads verified blobs.

No cloud document mutation commands are exposed. Tokens are encrypted with
Windows DPAPI or a separately mounted Linux encryption key in a private profile.
The community protocol is pinned to the audited rmapi v0.0.35 cache schema.
"""
from __future__ import annotations

import contextlib
import csv
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile

PAIR_URL = "https://my.remarkable.com/device/browser/connect"
CLIENT = Path(__file__).resolve().parents[2] / ".tools" / "rmapi" / ("rmapi.exe" if os.name == "nt" else "rmapi")
HASH = re.compile(r"[0-9a-f]{64}\Z")
LIMIT = 8 * 1024**3


class CloudError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise CloudError("The cloud trial currently requires Windows token protection.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise CloudError("Windows could not protect/unlock the cloud account tokens.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def _tokens(data: bytes) -> dict[str, str]:
    # rmapi writes two simple YAML scalars. JWTs contain no YAML quoting or whitespace.
    values = dict(line.split(":", 1) for line in data.decode("ascii").splitlines() if ":" in line)
    result = {key: values.get(key, "").strip() for key in ("devicetoken", "usertoken")}
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in result.values()):
        raise CloudError("Cloud pairing did not save valid account tokens.")
    return result


def _private_profile(profile: Path) -> None:
    if os.name != "nt":
        from .cloudcredentials import private_directory
        private_directory(profile)
        return
    profile.mkdir(parents=True, exist_ok=True)
    identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, check=True)
    sid = next(csv.reader(io.StringIO(identity.stdout.decode("utf-8"))))[1]
    if not re.fullmatch(r"S-[0-9-]+", sid):
        raise CloudError("Could not determine the Windows account for token storage.")
    secured = subprocess.run(["icacls", str(profile), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
                             capture_output=True)
    if secured.returncode:
        raise CloudError("Could not restrict access to the cloud account profile.")


def _environment(work: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("RMAPI_", "REMARKSYNC_"))}
    env.update(RMAPI_CONFIG=str(work / "tokens.yaml"), RMAPI_CONCURRENT="4",
               LOCALAPPDATA=str(work), XDG_CACHE_HOME=str(work), TEMP=str(work), TMP=str(work),
               TMPDIR=str(work), RMAPI_TRACE="0")
    return env


def _documents(cache: dict) -> dict[str, dict]:
    if cache.get("CacheVersion") != 3 or cache.get("SchemaVersion") not in ("3", "4"):
        raise CloudError("Unsupported cloud cache schema; update the adapter before syncing.")
    if cache.get("Docs") and not HASH.fullmatch(cache.get("Hash", "")):
        raise CloudError("Invalid cloud root snapshot.")
    documents = {}
    for entry in cache.get("Docs") or []:
        identifier = str(uuid.UUID(entry["DocumentID"]))
        if identifier != entry["DocumentID"] or identifier in documents or not HASH.fullmatch(entry["Hash"]):
            raise CloudError("Invalid cloud document identity/revision.")
        metadata = entry["Metadata"]
        if metadata.get("type") in ("DocumentType", "CollectionType") and not metadata.get("deleted") and metadata.get("parent") != "trash":
            documents[identifier] = entry
    return documents


def _listed_documents(output: bytes, documents: dict[str, dict], *, pairing: bool) -> dict[str, dict]:
    text = output.decode("utf-8")
    if pairing:
        prompt = "Enter one-time code (go to https://my.remarkable.com/device/browser/connect): "
        if text.startswith(prompt):
            text = text[len(prompt):]
    try:
        listing = json.loads(text)
    except ValueError as exc:
        raise CloudError("Cloud client returned warnings or incomplete listing data; refusing acquisition.") from exc
    if not isinstance(listing, list):
        raise CloudError("Cloud listing was not a document array.")
    result = {}
    for item in listing:
        if item.get("type") not in ("DocumentType", "CollectionType") or item.get("id") in ("", "trash"):
            continue
        identifier = item["id"]
        if identifier not in documents or identifier in result:
            raise CloudError("Cloud listing does not match its fresh cached document index.")
        result[identifier] = documents[identifier]
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError("Cloud blob redirected unexpectedly; refusing to forward credentials.")


class CloudClient:
    def __init__(self, executable: Path | None = None, profile: Path | None = None):
        self.executable = Path(executable or os.environ.get("REMARKSYNC_CLOUD_CLIENT") or CLIENT).resolve()
        if profile is None:
            configured = os.environ.get("REMARKSYNC_CLOUD_PROFILE")
            if configured:
                profile = Path(configured)
            elif os.name == "nt":
                if not os.environ.get("LOCALAPPDATA"):
                    raise CloudError("Windows did not provide a private application-data location.")
                profile = Path(os.environ["LOCALAPPDATA"]) / "RemarkSync" / "cloud"
            else:
                profile = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "remarksync" / "cloud"
        self.profile = Path(profile).resolve()
        self.secret = self.profile / ("account.dpapi" if os.name == "nt" else "account.fernet")

    def _protect(self, data: bytes, *, decrypt: bool = False) -> bytes:
        if os.name == "nt":
            return _dpapi(data, decrypt=decrypt)
        from .cloudcredentials import protect
        return protect(data, self.profile, decrypt=decrypt)

    @property
    def paired(self) -> bool:
        return self.secret.is_file()

    @contextlib.contextmanager
    def snapshot(self, code: str | None = None):
        if not self.executable.is_file():
            raise CloudError("Cloud client missing. Run the platform's setup-cloud-client installer first.")
        if code is not None and not re.fullmatch(r"[A-Za-z0-9]{8}", code):
            raise CloudError("The one-time pairing code must contain eight letters/digits.")
        if code is None and not self.paired:
            raise CloudError("Pair your account first using Cloud sync… or remarksync cloud pair.")
        _private_profile(self.profile)
        # Lock covers token renewal and pairing across CLI/GUI processes.
        from .backupstore import writer_lock
        with writer_lock(self.profile):
            session_root = self.profile
            if os.name != "nt":
                from .cloudcredentials import private_directory, read_private
                if configured := os.environ.get("REMARKSYNC_CLOUD_SESSION_DIR"):
                    session_root = Path(configured)
                    private_directory(session_root)
                # Validate the encryption key before starting upstream pairing.
                self._protect(b"key-check")
            with tempfile.TemporaryDirectory(prefix="session-", dir=session_root) as temporary:
                work = Path(temporary)
                config = work / "tokens.yaml"
                if code is None:
                    if not self.paired:
                        raise CloudError("Pair your account first using Cloud sync… or remarksync cloud pair.")
                    saved = self.secret.read_bytes() if os.name == "nt" else read_private(self.secret)
                    with config.open("xb") as stream:
                        if os.name != "nt":
                            os.chmod(config, 0o600)
                        stream.write(self._protect(saved, decrypt=True))
                command = [str(self.executable)] + ([] if code is not None else ["-ni"]) + ["-json", "find", "/"]
                try:
                    process = subprocess.run(command, input=(code + "\n").encode() if code is not None else b"",
                                             cwd=work, env=_environment(work), capture_output=True, timeout=180)
                except subprocess.TimeoutExpired as exc:
                    raise CloudError("Cloud listing timed out; your existing archives are unchanged.") from exc
                if process.returncode:
                    # Upstream output can include account tokens. Never pass it to logs/UI.
                    raise CloudError("Cloud pairing/listing failed. Check internet access and pairing code/account authorization.")
                token_bytes = config.read_bytes()
                auth = _tokens(token_bytes)
                cache = json.loads((work / "rmapi" / "tree.cache").read_text(encoding="utf-8"))
                documents = _listed_documents(process.stdout, _documents(cache), pairing=code is not None)
                encrypted = self._protect(token_bytes)
                # Sessions may be in RAM; replacement must use the profile's filesystem.
                descriptor, name = tempfile.mkstemp(prefix=".account-", dir=self.profile)
                protected = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(encrypted)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(protected, self.secret)
                finally:
                    protected.unlink(missing_ok=True)
                yield documents, auth["usertoken"]

    def list(self, code: str | None = None) -> list[dict]:
        with self.snapshot(code) as (documents, _token):
            return [{"id": identifier, "name": entry["Metadata"].get("visibleName", "Untitled"),
                     "file_type": entry["Content"].get("fileType", "notebook"), "revision": entry["Hash"]}
                    for identifier, entry in sorted(documents.items(), key=lambda pair: pair[1]["Metadata"].get("visibleName", ""))
                    if entry["Metadata"].get("type") == "DocumentType"]

    def download(self, entry: dict, token: str, destination: Path) -> None:
        """Fetch exact hashes into a fixed filename, never a remote display name."""
        from .backup import safe_extract_names
        files = entry["Files"]
        if entry.get("Subfiles") != len(files):
            raise CloudError("Cloud file count differs from the document index.")
        names = [item["DocumentID"] for item in files]
        safe_extract_names(names, destination.parent / "validation")
        if len(files) > 200_000 or len({name.casefold() for name in names}) != len(names):
            raise CloudError("Cloud manifest has duplicate/excessive file entries.")
        identifier = entry["DocumentID"]
        if (f"{identifier}.metadata" not in names or (entry["Metadata"].get("type") == "DocumentType"
                                                     and f"{identifier}.content" not in names)):
            raise CloudError("Cloud document manifest is incomplete.")
        # Only this document's files are allowed; no sibling document injection.
        for item in files:
            name = item["DocumentID"]
            if not (name.startswith(identifier + ".") and "/" not in name) and not name.startswith(identifier + "/"):
                raise CloudError("Cloud file belongs to a different document.")
            if ("\\" in name or any(ord(c) < 32 for c in name) or not name.isascii()
                    or any(part.endswith((".", " ")) for part in name.split("/"))
                    or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..")
                           or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)
                           for part in name.split("/"))
                    or not HASH.fullmatch(item["Hash"]) or not isinstance(item["Size"], int) or item["Size"] < 0):
                raise CloudError("Invalid cloud file manifest entry.")
        if sum(item["Size"] for item in files) > LIMIT:
            raise CloudError("Cloud document exceeds the archive size limit.")
        opener = urllib.request.build_opener(_NoRedirect())
        created = False
        try:
            with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
                created = True
                for item in files:
                    request = urllib.request.Request("https://internal.cloud.remarkable.com/sync/v3/files/" + item["Hash"],
                                                     headers={"Authorization": "Bearer " + token, "rm-filename": item["DocumentID"],
                                                              "User-Agent": "rmapi"})
                    digest, size = hashlib.sha256(), 0
                    with opener.open(request, timeout=60) as response, archive.open(item["DocumentID"], "w", force_zip64=True) as out:
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > item["Size"]:
                                raise CloudError("Cloud file exceeded its declared size.")
                            digest.update(chunk)
                            out.write(chunk)
                    if size != item["Size"] or digest.hexdigest() != item["Hash"]:
                        raise CloudError("Cloud file checksum/size mismatch; refusing to publish.")
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise


def trial(client: CloudClient, identifier: str, destination: Path) -> dict:
    """One immutable revision + preservation PDF, separated from the SSH backup."""
    from .backup import open_rmdoc
    from .pdfexport import export_pdf
    identifier = str(uuid.UUID(identifier))
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".trial-", dir=destination) as temporary:
        stage = Path(temporary)
        with client.snapshot() as (documents, token):
            entry = documents.get(identifier)
            if entry is None:
                raise CloudError("Selected document is absent/deleted in this cloud listing.")
            if entry["Content"].get("fileType") not in ("pdf", "epub"):
                raise CloudError("Choose a PDF/EPUB for the preservation trial.")
            revision = entry["Hash"]
            target = destination / identifier / revision
            if target.exists():
                report = json.loads((target / "trial.json").read_text(encoding="utf-8"))
                if report["document"] != identifier or report["revision"] != revision:
                    raise CloudError("Existing cloud archive identity mismatch.")
                for name in ("document.rmdoc", "annotated.pdf"):
                    if _digest(target / name) != report["checksums"][name]:
                        raise CloudError("Existing cloud archive checksum mismatch; retained for investigation.")
                return dict(report, cached=True)
            bundle = stage / "revision"
            bundle.mkdir()
            archive = bundle / "document.rmdoc"
            client.download(entry, token, archive)
            doc = open_rmdoc(archive, stage / "raw")
            if doc.uuid != identifier or doc.file_type != entry["Content"].get("fileType"):
                raise CloudError("Downloaded document identity/content differs from the cloud snapshot.")
            pdf = export_pdf(doc, bundle / "annotated.pdf", text_layout="tablet")
            report = {"document": identifier, "name": entry["Metadata"].get("visibleName", "Untitled"),
                      "revision": revision, "path": str(target / "annotated.pdf"), "pages": pdf.pages,
                      "warnings": pdf.warnings, "cloud_document_writes": 0,
                      "files": [{"path": item["DocumentID"], "sha256": item["Hash"], "size": item["Size"]}
                                for item in entry["Files"]],
                      "checksums": {name: _digest(bundle / name) for name in ("document.rmdoc", "annotated.pdf")}}
            (bundle / "trial.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            # A new listing uses a new cache: never publish an obsolete/torn source.
            # Release the credential lock before taking the second snapshot below.
        with client.snapshot() as (current, _token):
            if current.get(identifier, {}).get("Hash") != revision:
                raise CloudError("Document changed in the cloud during the trial; retry after tablet sync finishes.")
        target.parent.mkdir(parents=True, exist_ok=True)
        bundle.rename(target)
        return dict(report, cached=False)
