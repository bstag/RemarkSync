"""Cloud acquisition and sending. rmapi pairs/lists; Python downloads verified blobs.

Reading never changes a cloud document. The one mutation exposed is :func:`send`,
which uploads a single validated archive through rmapi's ``put`` and only when a
caller asks for it; no general rmapi command is reachable. Tokens are encrypted
with Windows DPAPI or a separately mounted Linux encryption key in a private
profile. The community protocol is pinned to the audited rmapi v0.0.35 cache schema.
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
import shutil
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

    def _listing(self, work: Path, *, pairing: bool = False, code: str | None = None):
        """One fresh ``find /`` in this session: active documents and every indexed id."""
        (work / "rmapi" / "tree.cache").unlink(missing_ok=True)
        command = [str(self.executable)] + ([] if pairing else ["-ni"]) + ["-json", "find", "/"]
        try:
            process = subprocess.run(command, input=(code + "\n").encode() if pairing else b"",
                                     cwd=work, env=_environment(work), capture_output=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise CloudError("Cloud listing timed out; your existing archives are unchanged.") from exc
        if process.returncode:
            # Upstream output can include account tokens. Never pass it to logs/UI.
            raise CloudError("Cloud pairing/listing failed. Check internet access and pairing code/account authorization.")
        cache = json.loads((work / "rmapi" / "tree.cache").read_text(encoding="utf-8"))
        documents = _listed_documents(process.stdout, _documents(cache), pairing=pairing)
        return documents, {entry["DocumentID"] for entry in cache.get("Docs") or []}

    @contextlib.contextmanager
    def _session(self, code: str | None = None):
        """A private working directory holding unlocked tokens and one fresh listing."""
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
                documents, identifiers = self._listing(work, pairing=code is not None, code=code)
                token_bytes = config.read_bytes()
                auth = _tokens(token_bytes)
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
                yield documents, auth["usertoken"], work, identifiers

    @contextlib.contextmanager
    def snapshot(self, code: str | None = None):
        with self._session(code) as (documents, token, _work, _identifiers):
            yield documents, token

    @contextlib.contextmanager
    def sender(self):
        """A listing plus the two things sending needs: ``put`` and a re-listing.

        ``put(filename, path, force)`` uploads a file lying in the session's
        working directory and reports only whether the client exited cleanly;
        its output can hold tokens and is discarded. The caller must confirm the
        outcome from ``relist()``, never from the exit status.

        The file is named, never given as a path: the client takes the
        document's name from this argument and on Windows does not strip the
        directories, so a path becomes the name (seen live, 2026-11-21).
        """
        with self._session() as (documents, token, work, identifiers):
            def put(filename: str, path: str, force: bool) -> bool:
                if Path(filename).name != filename or not (work / filename).is_file():
                    raise CloudError("Only a file in the session directory can be uploaded.")
                command = [str(self.executable), "-ni", "put"] + (["--force"] if force else []) + [filename, path]
                try:
                    return not subprocess.run(command, input=b"", cwd=work, env=_environment(work),
                                              capture_output=True, timeout=900).returncode
                except subprocess.TimeoutExpired:
                    return False
            yield documents, identifiers, token, work, put, lambda: self._listing(work)

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


# ------------------------------------------------------------------- sending

_MEMBER = re.compile(r"[A-Za-z0-9_.-]+\Z")
_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?\Z")


def archive_files(archive: Path) -> tuple[str, dict[str, str]]:
    """Validate an archive for sending; its document id and each member's SHA-256.

    Stricter than opening one for viewing: the id is taken from the archive and
    every member is uploaded under its own name, so anything that is not plainly
    one document's files is refused before the account is touched.
    """
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            names = [entry.filename for entry in entries]
            if not names or len(names) > 200_000 or len({name.casefold() for name in names}) != len(names):
                raise CloudError("Archive has no files or duplicate/excessive entries.")
            if sum(entry.file_size for entry in entries) > LIMIT:
                raise CloudError("Archive exceeds the size limit.")
            contents = [name for name in names if name.endswith(".content")]
            if len(contents) != 1 or "/" in contents[0]:
                raise CloudError("Archive must hold exactly one document.")
            identifier = contents[0][:-len(".content")]
            try:
                valid = str(uuid.UUID(identifier)) == identifier
            except ValueError:
                valid = False
            if not valid or identifier + ".metadata" not in names:
                raise CloudError("Archive document identity/metadata is missing or invalid.")
            hashes = {}
            for entry in entries:
                name = entry.filename
                parts = name.split("/")
                inside = (len(parts) == 1 and name.startswith(identifier + ".")) or (len(parts) > 1 and parts[0] == identifier)
                if (not inside or entry.is_dir() or any(not _MEMBER.match(part) or part in (".", "..")
                                                         or part.endswith(".") or _RESERVED.match(part) for part in parts)):
                    raise CloudError("Archive holds a file that does not belong to its document.")
                with bundle.open(entry) as stream:
                    hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            for name in (identifier + ".metadata", identifier + ".content"):
                if not isinstance(json.loads(bundle.read(name)), dict):
                    raise CloudError("Archive metadata/content is not a JSON object.")
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise CloudError("Archive could not be read as a tablet document.") from exc
    return identifier, hashes


def _same_document(entry: dict, hashes: dict[str, str]) -> bool:
    """Whether a cloud entry holds exactly these files. The client rewrites
    ``.metadata`` (name, folder, time) on upload, so that one file is exempt."""
    cloud = {item["DocumentID"]: item["Hash"] for item in entry["Files"]}
    skip = entry["DocumentID"] + ".metadata"
    return skip in cloud and ({name: value for name, value in cloud.items() if name != skip}
                              == {name: value for name, value in hashes.items() if name != skip})


def _children(documents: dict[str, dict], folder: str) -> list[dict]:
    return [entry for entry in documents.values() if (entry["Metadata"].get("parent") or "") == folder]


def _named(documents: dict[str, dict], folder: str, name: str) -> list[dict]:
    return [entry for entry in _children(documents, folder)
            if (entry["Metadata"].get("visibleName") or "").casefold() == name.casefold()]


def _folder_path(documents: dict[str, dict], folder: str) -> str:
    """The client addresses folders by name, so a folder is only usable when
    its path is spelled unambiguously."""
    parts: list[str] = []
    seen = set()
    while folder:
        entry = documents.get(folder)
        if entry is None or entry["Metadata"].get("type") != "CollectionType" or folder in seen:
            raise CloudError("The destination folder is not in the cloud library.")
        seen.add(folder)
        name = entry["Metadata"].get("visibleName") or ""
        parent = entry["Metadata"].get("parent") or ""
        if (not name or "/" in name or name in (".", "..") or name != name.strip()
                or len(_named(documents, parent, name)) != 1):
            raise CloudError(f"The folder '{name}' cannot be addressed unambiguously; rename it on the tablet first.")
        parts.append(name)
        folder = parent
    return "/" + "/".join(reversed(parts))


def send_name(name: str) -> str:
    """The client names the document after the uploaded file, so the name has
    to be a filename on every platform."""
    if (not name or len(name) > 180 or name != name.strip() or name.endswith(".") or _RESERVED.match(name)
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)):
        raise CloudError('That name cannot be sent: avoid < > : " / \\ | ? * and leading/trailing spaces or dots.')
    return name


def send(client: CloudClient, archive: Path, name: str, *, folder: str = "",
         replace: dict[str, str] | None = None, keep: Path | None = None) -> dict:
    """Upload one archive as a new document, or replace the cloud document it
    was edited from.

    A new document is refused when its name is taken in the destination. One
    whose id is already in the cloud with the same files is reported as already
    sent, which is what makes a retry after a timeout safe.

    Replacing needs ``replace``: the SHA-256 of every file of the copy the edit
    was made from, by archive name. If the cloud's files are anything else the
    tablet has changed the document since, and nothing is sent. (Files, not the
    cloud's document hash: the tablet and the client derive that differently,
    so it cannot be recomputed from a local copy.) The client replaces by deleting and uploading
    again, with nothing to undo the delete, so the cloud's current revision is
    first saved under ``keep``; a failed upload names that file.

    The outcome is always read back from a fresh listing. The client can exit
    cleanly without having written anything.
    """
    archive = Path(archive)
    name = send_name(name)
    identifier, hashes = archive_files(archive)
    replacing = replace is not None
    with client.sender() as (documents, identifiers, token, work, put, relist):
        saved = None
        if replacing:
            entry = documents.get(identifier)
            if entry is None or entry["Metadata"].get("type") != "DocumentType":
                raise CloudError("This document is not in the cloud library, so there is nothing to replace. "
                                 "Send it as a new document.")
            if _same_document(entry, hashes):
                return {"status": "already-sent", "document": identifier, "revision": entry["Hash"], "name": name}
            if {item["DocumentID"]: item["Hash"] for item in entry["Files"]} != replace:
                raise CloudError("The document changed on the tablet after this copy was synced. Nothing was sent; "
                                 "sync, then make the edit again.")
            if (entry["Metadata"].get("visibleName") or "") != name:
                raise CloudError("A replacement keeps the document's current name; rename it on the tablet.")
            folder = entry["Metadata"].get("parent") or ""
            if len(_named(documents, folder, name)) != 1:
                raise CloudError("Another item in the same folder has this name, so the replacement would be ambiguous.")
            if keep is None:
                raise CloudError("Replacing needs somewhere to keep the current cloud revision.")
            saved = Path(keep).resolve() / identifier / (entry["Hash"] + ".rmdoc")
            if not saved.is_file():
                saved.parent.mkdir(parents=True, exist_ok=True)
                client.download(entry, token, saved)
        else:
            if identifier in identifiers:
                entry = documents.get(identifier)
                if entry is not None and _same_document(entry, hashes):
                    return {"status": "already-sent", "document": identifier, "revision": entry["Hash"], "name": name}
                raise CloudError("A different document with this identity is already in the cloud (or its trash). "
                                 "Send a new copy instead.")
            if _named(documents, folder, name):
                raise CloudError(f"'{name}' already exists in that folder. Choose another name.")
        path = _folder_path(documents, folder)
        staged = work / (name + ".rmdoc")
        shutil.copyfile(archive, staged)
        clean = put(staged.name, path, replacing)
        try:
            after, _identifiers = relist()
        except CloudError as exc:
            raise CloudError("The upload was attempted but the cloud could not be re-read to confirm it. "
                             "Send again: an upload that did arrive will be recognised, not duplicated."
                             + (f" The previous revision is kept at {saved}." if saved else "")) from exc
        entry = after.get(identifier)
        if entry is None or not _same_document(entry, hashes):
            if replacing and entry is None:
                raise CloudError("The replacement failed after the original was removed from the cloud. "
                                 f"The previous revision is kept at {saved}; send that archive as a new document to restore it.")
            raise CloudError("The cloud did not accept the upload; nothing changed." if not clean else
                             "The upload reported success but the document is not in the cloud. Nothing changed; try again.")
        arrived = entry["Metadata"].get("visibleName") or ""
        if arrived != name or (entry["Metadata"].get("parent") or "") != folder:
            raise CloudError(f"The document was uploaded but is named '{arrived}' or sits in another folder. "
                             "Rename or move it on the tablet.")
        return {"status": "sent", "document": identifier, "revision": entry["Hash"], "name": name,
                "replaced": replacing, "kept": str(saved) if saved else None}
