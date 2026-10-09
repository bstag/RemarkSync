"""Download a central preservation library into an independent desktop store.

No pairing or PDF rendering happens here. Source revisions and renderings are
retained by hash; absence on the server never removes a local document.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
import zipfile

from .backup import safe_extract_names
from .backupstore import publish, recover, write_json, writer_lock
from .cloud import CloudError, HASH, _digest
from .cloudsync import STATE, _catalog, _now, _verify_files, preservation_pdf
from .pull import _document_entries, _move_document, _validate_document, local_signature

SERVER_ENV = "REMARKSYNC_SERVER"
DEFAULT_DESTINATION = Path(__file__).resolve().parents[2] / "output" / "server-sync"
MARKER = ".remarksync-server-store.json"
MAX_ARTIFACT = 8 * 1024**3


def default_server() -> str:
    """This installation's server address. No address is built into the package."""
    return os.environ.get(SERVER_ENV, "").strip()


def server_url(value: str) -> str:
    parts = urlsplit(value.strip())
    # TLS for network destinations; HTTP is available only for local transport tests.
    if (parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password
            or parts.path not in ("", "/") or parts.query or parts.fragment
            or (parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "::1", "localhost"))):
        raise CloudError("Use the HTTPS address of your private server, without a path or login details.")
    try:
        parts.port
    except ValueError as exc:
        raise CloudError("Invalid server port.") from exc
    return value.strip().rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise CloudError("The private server redirected the request; check its address.")


class ServerClient:
    def __init__(self, base: str):
        self.base = server_url(base)
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _open(self, path):
        try:
            return self.opener.open(self.base + path, timeout=60)
        except (urllib.error.URLError, OSError) as exc:
            raise CloudError("Cannot read the private server. Check Tailscale and the server; local copies are retained.") from exc

    def manifest(self) -> dict:
        with self._open("/manifest") as response:
            data = response.read(32 * 1024**2 + 1)
        if len(data) > 32 * 1024**2:
            raise CloudError("Server manifest exceeds its size limit.")
        try:
            return validate_manifest(json.loads(data))
        except (ValueError, TypeError, KeyError) as exc:
            raise CloudError("Invalid server library manifest; local copies are retained.") from exc

    def download(self, identifier, revision, name, digest, destination):
        checksum, size = hashlib.sha256(), 0
        with self._open(f"/revisions/{identifier}/{revision}/{name}") as response, destination.open("xb") as out:
            if response.status != 200:
                raise CloudError("Incomplete server response; local copies are retained.")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARTIFACT:
                    raise CloudError("Server artifact exceeds its size limit.")
                checksum.update(chunk)
                out.write(chunk)
        if checksum.hexdigest() != digest:
            raise CloudError("Server artifact checksum mismatch; local copies are retained.")


def validate_manifest(data: dict) -> dict:
    if (data.get("format") != "remarksync-library-v1" or not isinstance(data.get("documents"), dict)
            or data.get("status") not in ("ok", "partial", "running", "error")):
        raise ValueError("Invalid library format/status")
    for identifier, record in data["documents"].items():
        if str(uuid.UUID(identifier)) != identifier or record.get("document") != identifier:
            raise ValueError("Invalid document identity")
        if record.get("kind") not in ("DocumentType", "CollectionType") or not isinstance(record.get("name"), str):
            raise ValueError("Invalid document metadata")
        for field in ("revision", "archive_sha256"):
            if not isinstance(record.get(field), str) or not HASH.fullmatch(record[field]):
                raise ValueError("Invalid artifact hash")
        if record.get("pdf_sha256") is not None and not HASH.fullmatch(record["pdf_sha256"]):
            raise ValueError("Invalid PDF hash")
        files = record.get("files")
        if not isinstance(files, list) or not files or len(files) > 200_000:
            raise ValueError("Invalid raw manifest")
        seen, size = set(), 0
        for item in files:
            name = item["path"]
            if (not isinstance(name, str) or "\\" in name or name.startswith("/")
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or not (name.split("/")[0] == identifier or name.split("/")[0].startswith(identifier + "."))
                    or name in seen or not HASH.fullmatch(item["sha256"])
                    or type(item["size"]) is not int or item["size"] < 0):
                raise ValueError("Invalid source file")
            seen.add(name)
            size += item["size"]
        safe_extract_names(seen, Path(tempfile.gettempdir()) / "remarksync-manifest-check")
        if size > MAX_ARTIFACT or f"{identifier}.metadata" not in seen:
            raise ValueError("Incomplete or oversized source")
        if not isinstance(record.get("warnings", []), list) or any(not isinstance(w, str) for w in record.get("warnings", [])):
            raise ValueError("Invalid export warnings")
    return data


def _inside(root, path):
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
        raise CloudError("Local sync path contains a link or escapes the server store.")
    return path


def _extract(archive: Path, raw: Path, record: dict):
    expected = {item["path"]: item["size"] for item in record["files"]}
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        safe_extract_names([item.filename for item in members], raw)
        files = [item for item in members if not item.is_dir()]
        if (len(members) > 200_000 or len(files) != len(expected)
                or {item.filename: item.file_size for item in files} != expected
                or any(stat.S_ISLNK(item.external_attr >> 16) for item in members)):
            raise CloudError("Archive contents differ from the server manifest.")
        # Ignore archive permissions; write only the manifest's verified files.
        for item in files:
            target = raw / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(item) as src, target.open("xb") as out:
                shutil.copyfileobj(src, out)
    if not _verify_files(raw, record["files"]):
        raise CloudError("Extracted server files failed verification.")
    _validate_document(raw, record["document"], local_signature(raw, record["document"]))


def sync(client: ServerClient, destination: Path, *, progress=None) -> dict:
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists() and any(root.iterdir()):
        raise CloudError("Choose an empty destination or this server's existing desktop store; backups and direct-cloud stores stay separate.")
    with writer_lock(root):
        _inside(root, marker)
        if marker.exists():
            saved = json.loads(marker.read_text())
            if saved != {"format": "remarksync-server-v1", "server": client.base}:
                raise CloudError("This desktop store belongs to a different source; choose a separate destination.")
        else:
            write_json(marker, {"format": "remarksync-server-v1", "server": client.base})
        raw_root = _inside(root, root / "raw")
        raw_root.mkdir(exist_ok=True)
        state_path = _inside(root, root / STATE)
        state = json.loads(state_path.read_text()) if state_path.exists() else {"documents": {}}
        result = {"downloaded": [], "changed": [], "unchanged": [], "missing": [], "failed": [], "pdf_failed": [],
                  "preserved": [], "raw_library": str(raw_root), "cloud_document_writes": 0}
        with writer_lock(raw_root):
            recover(raw_root)
            remote = client.manifest()
            state.update(status="running", last_attempt=_now(), server_status=remote["status"],
                         server_last_success=remote.get("last_success"))
            write_json(state_path, state)
            with tempfile.TemporaryDirectory(prefix=".server-sync-", dir=root) as temporary:
                for index, (identifier, record) in enumerate(remote["documents"].items()):
                    if progress:
                        progress({"name": record["name"], "done": index, "total": len(remote["documents"])})
                    previous = state["documents"].get(identifier)
                    try:
                        target = _inside(root, root / "revisions" / identifier / record["revision"])
                        archive = _inside(root, target / "document.rmdoc")
                        current_raw = (previous and previous["revision"] == record["revision"]
                                       and previous["archive_sha256"] == record["archive_sha256"]
                                       and _verify_files(raw_root, record["files"]))
                        same_pdf = previous and previous.get("pdf_sha256") == record.get("pdf_sha256")
                        if (current_raw and same_pdf and _digest(archive) == record["archive_sha256"]
                                and (not record.get("pdf_sha256") or _digest(_inside(root, preservation_pdf(target, record))) == record["pdf_sha256"])):
                            state["documents"][identifier] = record
                            result["unchanged"].append(identifier)
                            continue
                        stage = Path(temporary) / identifier
                        stage.mkdir()
                        incoming = stage / "document.rmdoc"
                        if archive.exists():
                            if _digest(archive) != record["archive_sha256"]:
                                raise CloudError("Cached server archive is corrupt; retained for investigation.")
                        else:
                            client.download(identifier, record["revision"], "document.rmdoc", record["archive_sha256"], incoming)
                        raw = stage / "raw"
                        raw.mkdir()
                        _extract(archive if archive.exists() else incoming, raw, record)
                        pdf = None
                        if record.get("pdf_sha256"):
                            pdf = _inside(root, target / "renderings" / record["pdf_sha256"] / "annotated.pdf")
                            if pdf.exists():
                                if _digest(pdf) != record["pdf_sha256"]:
                                    raise CloudError("Cached server PDF is corrupt; retained for investigation.")
                            else:
                                client.download(identifier, record["revision"], "annotated.pdf", record["pdf_sha256"], stage / "annotated.pdf")
                        # Publish artifacts only after every downloaded byte was verified.
                        target.mkdir(parents=True, exist_ok=True)
                        if not archive.exists():
                            os.link(incoming, archive)
                        if pdf is not None and not pdf.exists():
                            pdf.parent.mkdir(parents=True, exist_ok=True)
                            os.link(stage / "annotated.pdf", pdf)
                        if not current_raw:
                            publish(raw_root, raw, identifier, _document_entries, _move_document)
                        state["documents"][identifier] = record
                        write_json(state_path, state)
                        result["changed" if previous else "downloaded"].append(identifier)
                        if pdf:
                            result["preserved"].append(str(pdf))
                    except Exception as exc:
                        result["failed"].append({"document": identifier, "error": str(exc) if isinstance(exc, CloudError)
                                                 else "Server download/publication failed; prior copies are retained."})
            result["missing"] = sorted(set(state["documents"]) - set(remote["documents"]))
        result["exports_available"] = sum(bool(r.get("pdf_sha256")) for r in state["documents"].values())
        state["status"] = "partial" if result["failed"] or remote["status"] != "ok" else "ok"
        state["last_report"] = result
        if state["status"] == "ok":
            state["last_success"] = _now()
        write_json(state_path, state)
        _catalog(root, state)
        return dict(result, status=state["status"], last_success=state.get("last_success"),
                    server_status=remote["status"], server_last_success=remote.get("last_success"))
