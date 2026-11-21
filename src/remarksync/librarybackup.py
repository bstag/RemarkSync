"""Stream a credential-free retained library backup and verify/restore it offline.

The mutable raw projection is rebuilt from verified archives during restoration.
Only immutable document archives, completed PDFs and allowlisted metadata are
selected. A completed ZIP plus its final checksum manifest establishes success.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid
import zipfile

from .backupstore import read_json
from .cloudsync import MARKER
from .serversync import _extract, validate_manifest
from .store import CLOUD_STORE_FORMAT, HASH, LIBRARY_FORMAT, MAX_ARTIFACT, MAX_METADATA, PUBLIC_RECORD_FIELDS, STATE
from .store import copy_hashed, open_nofollow, preservation_pdf
from .store import catalog as _catalog, digest as _digest, now as _now

FORMAT = "remarksync-backup-v1"
MAX_FILES = 100_000
MAX_BACKUP_BYTES = 100 * 1024**3
# What the server publishes about a record, plus when it was acquired and
# rendered. ``absent_since`` is among the published fields, so a restored
# library still knows which documents the tablet had removed.
RECORD_FIELDS = PUBLIC_RECORD_FIELDS + ("acquired_at", "pdf_rendered_at")
RESTORE = b"""RemarkSync whole-library backup

Includes retained raw document revisions, completed preservation PDFs and
checksum-verified restoration metadata. Account profiles/keys are excluded.
The current raw library is rebuilt from its .rmdoc archives when restoring.

With RemarkSync installed, verify this download:
    remarksync backup verify BACKUP.zip
Restore into a NEW directory without contacting the server or cloud:
    remarksync backup restore BACKUP.zip NEW_DIRECTORY
Open NEW_DIRECTORY/raw in RemarkSync. PDFs remain under revisions/.
See docs/backups.md for recovery details. Keep the original ZIP independently.
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _record(identifier, record):
    clean = {key: record[key] for key in RECORD_FIELDS if key in record}
    clean["files"] = [{key: item[key] for key in ("path", "size", "sha256")} for item in record["files"]]
    validate_manifest({"format": LIBRARY_FORMAT, "status": "ok", "documents": {identifier: clean}})
    return clean


def _open(root: Path, path: Path):
    stream = open_nofollow(root, path, "Backup source contains a link or escapes its library.")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError("Backup source is not a regular file.")
    return stream


@dataclass
class Entry:
    name: str
    source: Path | bytes
    size: int
    sha256: str


@dataclass
class Snapshot:
    root: Path
    entries: list[Entry]
    created_at: str
    last_success: str | None
    documents: int
    revisions: int


def snapshot(root: Path, state: dict) -> Snapshot:
    """Capture published metadata and enumerate retained immutable artifacts.

    Acquisition can continue after capture. New revisions/renderings appearing
    later belong to the next backup; temporary and unfinished files are omitted.
    """
    root = root.resolve()
    if state.get("status") != "ok" or not state.get("documents"):
        raise ValueError("Wait for a complete successful sync before downloading a backup.")
    current = {identifier: _record(identifier, record) for identifier, record in state["documents"].items()}
    entries, retained, captured = [], set(), set()
    metadata_size = 0

    def data(name, value):
        nonlocal metadata_size
        if (name.endswith(".json") and len(value) > MAX_METADATA
                or metadata_size + len(value) > 64 * 1024**2):
            raise ValueError("Backup metadata exceeds its memory/size limit.")
        metadata_size += len(value)
        entries.append(Entry(name, value, len(value), hashlib.sha256(value).hexdigest()))

    def artifact(path, expected=None):
        name = path.relative_to(root).as_posix()
        if name in captured:
            return
        with _open(root, path) as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > MAX_ARTIFACT:
                raise ValueError("Backup artifact exceeds its size limit.")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected is not None and digest != expected:
            raise ValueError("Backup artifact failed verification; server copy retained.")
        captured.add(name)
        entries.append(Entry(name, path, size, digest))

    revision_root = root / "revisions"
    if revision_root.is_symlink():
        raise ValueError("Backup revision directory contains a link.")
    for document in sorted(revision_root.iterdir()):
        try:
            if str(uuid.UUID(document.name)) != document.name:
                continue
        except ValueError:
            continue
        if document.is_symlink() or not document.is_dir():
            raise ValueError("Invalid retained document directory.")
        for directory in sorted(document.iterdir()):
            if not HASH.fullmatch(directory.name):
                continue
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("Invalid retained revision directory.")
            identifier, revision = document.name, directory.name
            published = current.get(identifier)
            if published and published["revision"] == revision:
                record = published  # The captured state defines its selected rendering.
            else:
                with _open(root, directory / "revision.json") as stream:
                    value = stream.read(MAX_METADATA + 1)
                if len(value) > MAX_METADATA:
                    raise ValueError("Retained revision metadata exceeds its size limit.")
                record = _record(identifier, json.loads(value))
            if record["revision"] != revision:
                raise ValueError("Retained revision identity mismatch.")
            artifact(directory / "document.rmdoc", record["archive_sha256"])
            data(f"revisions/{identifier}/{revision}/revision.json", _json(record))
            retained.add((identifier, revision))
            if record.get("pdf_sha256"):
                selected = preservation_pdf(directory, record)
                artifact(selected, record["pdf_sha256"])
                legacy = directory / "annotated.pdf"
                if legacy != selected and legacy.exists():
                    artifact(legacy)
            renderings = directory / "renderings"
            if renderings.is_symlink():
                raise ValueError("Retained rendering directory contains a link.")
            if renderings.exists():
                for rendering in sorted(renderings.iterdir()):
                    if HASH.fullmatch(rendering.name):
                        artifact(rendering / "annotated.pdf", rendering.name)
            if len(entries) > MAX_FILES - 4:
                raise ValueError("Backup has too many retained files.")
    if not {(identifier, record["revision"]) for identifier, record in current.items()} <= retained:
        raise ValueError("A published revision is missing from retained storage.")
    clean_state = {"status": "ok", "last_success": state.get("last_success"), "documents": current}
    data(STATE, _json(clean_state))
    data(MARKER, _json({"format": CLOUD_STORE_FORMAT}))
    data("RESTORE.txt", RESTORE)
    if sum(entry.size for entry in entries) > MAX_BACKUP_BYTES:
        raise ValueError("Backup exceeds the 100 GiB supported size limit.")
    return Snapshot(root, entries, _now(), state.get("last_success"), len(current), len(retained))


class _Sink:
    """Non-seekable ZIP output; no complete archive is buffered in memory."""
    def __init__(self, stream):
        self.stream, self.offset = stream, 0

    def write(self, value):
        self.stream.write(value)
        self.offset += len(value)
        return len(value)

    def tell(self):
        return self.offset

    def flush(self):
        self.stream.flush()


def write_snapshot(plan: Snapshot, output) -> None:
    """Stream verified entries; an interrupted/changed source has no ZIP footer."""
    zipped = zipfile.ZipFile(_Sink(output), "w", compression=zipfile.ZIP_STORED, allowZip64=True)
    try:
        for entry in plan.entries:
            info = zipfile.ZipInfo(entry.name)
            info.file_size, info.external_attr = entry.size, (stat.S_IFREG | 0o600) << 16
            with (io.BytesIO(entry.source) if isinstance(entry.source, bytes) else _open(plan.root, entry.source)) as source:
                with zipped.open(info, "w", force_zip64=True) as target:
                    size, checksum = copy_hashed(source, target, entry.size)
            if size != entry.size or checksum != entry.sha256:
                raise ValueError("Backup source changed during download.")
        manifest = {"format": FORMAT, "created_at": plan.created_at, "last_success": plan.last_success,
                    "current_entries": plan.documents, "retained_revisions": plan.revisions,
                    "files": [{"path": entry.name, "size": entry.size, "sha256": entry.sha256} for entry in plan.entries]}
        metadata = _json(manifest)
        if len(metadata) > MAX_METADATA:
            raise ValueError("Backup checksum manifest exceeds its size limit.")
        zipped.writestr("backup-manifest.json", metadata)
        zipped.close()
    except BaseException:
        # Do not turn a partial stream into a seemingly complete ZIP on cleanup.
        zipped.fp = None
        raise


def _allowed(name):
    if name in (STATE, MARKER, "RESTORE.txt"):
        return True
    parts = name.split("/")
    if len(parts) not in (4, 6) or parts[0] != "revisions":
        return False
    try:
        if str(uuid.UUID(parts[1])) != parts[1] or not HASH.fullmatch(parts[2]):
            return False
    except ValueError:
        return False
    return (len(parts) == 4 and parts[3] in ("document.rmdoc", "revision.json", "annotated.pdf")
            or len(parts) == 6 and parts[3] == "renderings" and HASH.fullmatch(parts[4]) and parts[5] == "annotated.pdf")


def _read_json(zipped, name):
    if zipped.getinfo(name).file_size > MAX_METADATA:
        raise ValueError("Backup metadata exceeds its size limit.")
    return json.loads(zipped.read(name))


def verify(archive: Path) -> dict:
    """Verify complete outer ZIP, hashes, all raw archives and current PDF pointers."""
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if (len(members) > MAX_FILES or len({item.filename for item in members}) != len(members)
                or sum(item.file_size for item in members) > MAX_BACKUP_BYTES + MAX_METADATA
                or any(item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) for item in members)):
            raise ValueError("Invalid or oversized backup file set.")
        manifest = _read_json(zipped, "backup-manifest.json")
        if manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), list):
            raise ValueError("Invalid backup manifest.")
        expected = {}
        for item in manifest["files"]:
            name = item["path"]
            if (not _allowed(name) or name in expected or type(item["size"]) is not int
                    or not 0 <= item["size"] <= MAX_ARTIFACT or not HASH.fullmatch(item["sha256"])):
                raise ValueError("Invalid backup member.")
            expected[name] = item
            if "/renderings/" in name and item["sha256"] != name.split("/")[4]:
                raise ValueError("Retained rendering hash differs from its path.")
        if set(expected) | {"backup-manifest.json"} != {item.filename for item in members}:
            raise ValueError("Backup file set differs from its manifest.")
        for name, item in expected.items():
            if zipped.getinfo(name).file_size != item["size"]:
                raise ValueError("Backup member size mismatch.")
            with zipped.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError("Backup member checksum mismatch.")
        if _read_json(zipped, MARKER) != {"format": CLOUD_STORE_FORMAT}:
            raise ValueError("Invalid restored store format.")
        state = _read_json(zipped, STATE)
        validate_manifest(dict(state, format=LIBRARY_FORMAT))
        records = {}
        for name in expected:
            if not name.endswith("/revision.json"):
                continue
            _, identifier, revision, _ = name.split("/")
            record = _record(identifier, _read_json(zipped, name))
            prefix = f"revisions/{identifier}/{revision}/"
            if record["revision"] != revision or expected[prefix + "document.rmdoc"]["sha256"] != record["archive_sha256"]:
                raise ValueError("Backup revision identity/checksum mismatch.")
            with zipped.open(prefix + "document.rmdoc") as stream, zipfile.ZipFile(stream) as raw:
                files = raw.infolist()
                source = {item["path"]: item for item in record["files"]}
                if (len(files) != len(source) or {item.filename: item.file_size for item in files}
                        != {name: item["size"] for name, item in source.items()}
                        or any(item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) for item in files)):
                    raise ValueError("Raw archive file set differs from its manifest.")
                for item in files:
                    with raw.open(item) as data:
                        if hashlib.file_digest(data, "sha256").hexdigest() != source[item.filename]["sha256"]:
                            raise ValueError("Raw archive source checksum mismatch.")
            if record.get("pdf_sha256"):
                rendered = prefix + f'renderings/{record["pdf_sha256"]}/annotated.pdf'
                selected = rendered if rendered in expected else prefix + "annotated.pdf"
                if expected[selected]["sha256"] != record["pdf_sha256"]:
                    raise ValueError("Selected preservation PDF checksum mismatch.")
            records[(identifier, revision)] = record
        for identifier, record in state["documents"].items():
            if _record(identifier, record) != records.get((identifier, record["revision"])):
                raise ValueError("Published backup state differs from its retained revision.")
        if any(name.startswith("revisions/") and tuple(name.split("/")[1:3]) not in records for name in expected):
            raise ValueError("Backup contains an artifact without revision metadata.")
        if manifest["current_entries"] != len(state["documents"]) or manifest["retained_revisions"] != len(records):
            raise ValueError("Backup completeness counts differ from its manifest.")
        return {"format": FORMAT, "last_success": state.get("last_success"), "files": len(expected),
                "documents": sum(record["kind"] == "DocumentType" for record in state["documents"].values()),
                "folders": sum(record["kind"] == "CollectionType" for record in state["documents"].values()),
                "retained_revisions": len(records), "pdfs": sum(name.endswith(".pdf") for name in expected)}


def restore(archive: Path, destination: Path) -> dict:
    """Restore into a new directory; verification failure never publishes a store."""
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore requires a new destination; existing libraries are never overwritten.")
    source_digest = _digest(archive)
    report = verify(archive)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "library"
        stage.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            manifest = _read_json(zipped, "backup-manifest.json")
            # Same archive is checked again while copying, including a possible local change after verification.
            for item in manifest["files"]:
                target = stage / item["path"]
                if not _allowed(item["path"]):
                    raise ValueError("Invalid restore path.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item["path"]) as source, target.open("xb") as out:
                    shutil.copyfileobj(source, out, 1024 * 1024)
                if target.stat().st_size != item["size"] or _digest(target) != item["sha256"]:
                    raise ValueError("Restore copy failed verification.")
        state = read_json(stage / STATE)
        raw = stage / "raw"
        raw.mkdir()
        for identifier, record in state["documents"].items():
            _extract(stage / "revisions" / identifier / record["revision"] / "document.rmdoc", raw, record)
        _catalog(stage, state)
        if _digest(archive) != source_digest:
            raise ValueError("Backup archive changed during restoration; refused publication.")
        if destination.exists():
            raise ValueError("Restore destination appeared during verification; refused publication.")
        stage.rename(destination)
    return dict(report, restored=str(destination), raw_library=str(destination / "raw"))
