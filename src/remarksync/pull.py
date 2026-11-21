"""Pull a raw backup from a reMarkable tablet over SSH (USB or Wi-Fi),
copying only what changed.

The tablet exposes its document store at ``root@10.11.99.1`` (USB) or its
Wi-Fi IP; the password is the one shown in Settings → Help → Copyrights
and licences unless a key was installed. It ships BusyBox, not rsync, so
the pull is built from ``ssh`` and ``tar``:

1. one ``tar`` over SSH brings every ``.metadata`` file (small, one per
   document) and every text conversion (``<uuid>.textconversion/*.json``,
   written when a page is converted to text on the device without the
   metadata changing);
2. each document's signature (metadata plus conversions) is compared
   with the local copy: the tablet rewrites the metadata on every edit,
   so a changed or missing signature marks a document to fetch;
3. a second ``tar`` brings just those documents' files (``<uuid>.*`` and
   ``<uuid>/``), replacing the local copies so removed pages vanish too.

Documents whose metadata is gone from the tablet are reported, and deleted
locally only when asked (``prune``). ``force`` fetches everything.

The tablet sits behind a seam (:class:`Tablet`) with two adapters: SSH for
real use and a local directory for tests, so the diff is tested without a
device.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .backup import safe_extract_names
from .backupstore import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_ENTRIES, copy_entry, has_unfinished, publish, recover
from .backupstore import validate_id, writer_lock

log = logging.getLogger(__name__)

DEFAULT_HOST = "root@10.11.99.1"
REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl/"
FETCH_BATCH = 40  # documents per tar invocation (keeps the command line short)

# progress(doc_uuid) -> keep going? Returning False stops the pull.
Progress = Callable[[str], bool | None]
Manifest = dict[str, int | None]  # relative file names/sizes, None for directories


class Tablet(Protocol):
    """Where a backup comes from: the device's document store."""

    def read_signatures(self) -> dict[str, bytes]:
        """A signature per document uuid: its ``.metadata`` plus any text
        conversions, so either kind of change is noticed."""

    def fetch(self, uuids: list[str], into: Path) -> Manifest:
        """Copy documents and return the complete source entry manifest."""


class SshTablet:
    """The real tablet, reached with the system ssh and its BusyBox tar."""

    def __init__(self, host: str = DEFAULT_HOST, remote_dir: str = REMOTE_DIR, timeout: int = 600):
        self.host = host
        self.remote_dir = remote_dir
        self.timeout = timeout

    @contextmanager
    def _tar(self, patterns: list[str]):
        remote = f"cd {shlex.quote(self.remote_dir)} && tar -cf - " + " ".join(patterns)
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", self.host, remote]
        log.info("ssh %s: %s", self.host, remote[:120])
        with tempfile.TemporaryFile() as data:
            proc = subprocess.run(cmd, stdout=data, stderr=subprocess.PIPE, timeout=self.timeout)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"ssh/tar failed with code {proc.returncode} — is the tablet connected and SSH enabled? "
                    f"{proc.stderr.decode(errors='replace').strip()[:200]}"
                )
            data.seek(0)
            yield data

    def read_signatures(self) -> dict[str, bytes]:
        files: dict[str, dict[str, bytes]] = {}
        with self._tar(["*.metadata", "$(ls -d *.textconversion 2>/dev/null)"]) as data, tarfile.open(fileobj=data, mode="r:") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.lstrip("./")
                uuid = name.split("/", 1)[0].split(".", 1)[0]
                handle = tar.extractfile(member)
                if handle is not None:
                    files.setdefault(uuid, {})[name] = handle.read()
        return {uuid: _signature(parts) for uuid, parts in files.items() if f"{uuid}.metadata" in parts}

    def fetch(self, uuids: list[str], into: Path) -> Manifest:
        for uuid in uuids:
            validate_id(uuid)
        manifest: Manifest = {}
        for start in range(0, len(uuids), FETCH_BATCH):
            batch = uuids[start : start + FETCH_BATCH]
            patterns = [f"{u}.*" for u in batch] + batch
            # a document without a page directory makes tar report an error
            # but still emit the rest; tolerate that by listing existing names
            with self._tar([f"$(ls -d {p} 2>/dev/null)" for p in patterns]) as data, tarfile.open(fileobj=data, mode="r:") as tar:
                manifest.update(_safe_extract(tar, into))
        return manifest


class LocalTablet:
    """A directory laid out like the tablet's store; the test adapter, and
    handy for copying between two local mirrors."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read_signatures(self) -> dict[str, bytes]:
        return {p.name[: -len(".metadata")]: local_signature(self.root, p.name[: -len(".metadata")]) for p in self.root.glob("*.metadata")}

    def fetch(self, uuids: list[str], into: Path) -> Manifest:
        manifest: Manifest = {}
        for u in uuids:
            for src in _document_entries(self.root, u):
                copy_entry(src, into / src.name)  # refuses links before copying anything
                manifest.update(_entry_manifest(self.root, src))
        return manifest


@dataclass
class PullReport:
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # gone from the tablet (deleted locally only with prune)
    completed: bool = True

    @property
    def fetched(self) -> int:
        return len(self.new) + len(self.changed)


def _signature(parts: dict[str, bytes]) -> bytes:
    """Metadata plus conversions, in a stable order."""
    return b"".join(name.encode() + b"\0" + data + b"\0" for name, data in sorted(parts.items()))


def local_signature(root: Path, uuid: str) -> bytes | None:
    """The signature of a local document, or None if it is not there."""
    meta = root / f"{uuid}.metadata"
    if not meta.exists():
        return None
    parts = {meta.name: meta.read_bytes()}
    folder = root / f"{uuid}.textconversion"
    if folder.is_dir():
        for f in sorted(folder.iterdir()):
            if f.is_file():
                parts[f"{folder.name}/{f.name}"] = f.read_bytes()
    return _signature(parts)


def plan(remote: dict[str, bytes], dest: Path) -> PullReport:
    """Which documents to fetch, given the tablet's signatures and the local copies."""
    report = PullReport()
    for uuid, data in remote.items():
        validate_id(uuid)
        local = local_signature(dest, uuid)
        if local is None:
            report.new.append(uuid)
        elif local != data:
            report.changed.append(uuid)
        else:
            report.unchanged.append(uuid)
    for local in dest.glob("*.metadata"):
        uuid = local.name[: -len(".metadata")]
        if uuid not in remote:
            report.removed.append(uuid)
    return report


def pull(
    dest: Path,
    tablet: Tablet | None = None,
    *,
    host: str = DEFAULT_HOST,
    progress: Progress | None = None,
    force: bool = False,
    prune: bool = False,
    dry_run: bool = False,
    timeout: int = 600,
) -> PullReport:
    """Bring ``dest`` up to date with the tablet. Returns what was done
    (or, with ``dry_run``, what would be: only the metadata is read)."""
    dest = Path(dest)
    tablet = tablet or SshTablet(host, timeout=timeout)
    if dry_run:
        if has_unfinished(dest):
            raise RuntimeError("an interrupted pull needs recovery; run a normal pull before a dry run")
        return _pull_locked(dest, tablet, progress, force, prune, True)
    dest.mkdir(parents=True, exist_ok=True)

    with writer_lock(dest):
        recover(dest)
        return _pull_locked(dest, tablet, progress, force, prune, dry_run)


def _pull_locked(dest: Path, tablet: Tablet, progress: Progress | None, force: bool, prune: bool, dry_run: bool) -> PullReport:
    remote = tablet.read_signatures()
    report = plan(remote, dest)
    todo = list(remote) if force else report.new + report.changed
    if force:
        report.changed, report.new, report.unchanged = [u for u in remote if u not in report.new], report.new, []
    if dry_run:
        return report

    staging = dest / STAGING_DIR
    for start in range(0, len(todo), FETCH_BATCH):
        batch = todo[start : start + FETCH_BATCH]
        # fetch into a staging directory first: a failed transfer must never
        # cost local data. Only then is the old copy replaced, wholesale, so
        # pages removed on the tablet vanish here too.
        staging.mkdir(exist_ok=True)
        batch_dir = Path(tempfile.mkdtemp(prefix="batch-", dir=staging))
        try:
            manifest = tablet.fetch(batch, batch_dir)
            # Validate the entire batch before publishing even one document.
            for uuid in batch:
                _validate_document(batch_dir, uuid, remote[uuid])
            if manifest is None or manifest != _folder_manifest(batch_dir):
                raise ValueError("incomplete fetch: source manifest does not match staged files")
            latest = tablet.read_signatures()
            if any(latest.get(uuid) != remote[uuid] for uuid in batch):
                raise ValueError("tablet changed during fetch; retry the pull")
            for uuid in batch:
                publish(dest, batch_dir, uuid, _document_entries, _move_document)
        finally:
            shutil.rmtree(batch_dir)
            if not any(staging.iterdir()):
                staging.rmdir()
        for uuid in batch:
            if progress is not None and progress(uuid) is False:
                report.completed = False
                return report

    if prune:
        latest = tablet.read_signatures()
        if any(uuid in latest for uuid in report.removed):
            raise ValueError("tablet changed before pruning; retry the pull")
        for uuid in report.removed:
            publish(dest, dest, uuid, _document_entries, _move_document, prune=True)
    return report


STAGING_DIR = ".remarksync-pull-tmp"


def _document_entries(root: Path, uuid: str) -> list[Path]:
    """Everything belonging to a document: ``<uuid>.*`` (files, and the
    ``.highlights`` and similar directories) plus the ``<uuid>/`` page dir."""
    validate_id(uuid)
    entries = sorted(root.glob(f"{uuid}.*"))
    if (root / uuid).is_dir():
        entries.append(root / uuid)
    return entries


def _move_document(src: Path, dest: Path, uuid: str) -> None:
    for p in _document_entries(src, uuid):
        shutil.move(str(p), str(dest / p.name))


def _safe_extract(tar: tarfile.TarFile, into: Path) -> Manifest:
    """Extract, refusing paths that would escape ``into``."""
    members = tar.getmembers()
    safe_extract_names([m.name for m in members], into)
    if any(not (m.isfile() or m.isdir()) for m in members):
        raise ValueError("refusing archive link or special member type")
    if len(members) > MAX_ARCHIVE_ENTRIES or sum(m.size for m in members) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds extraction limits")
    # Write only regular files/directories: no tar link or permission metadata
    # is applied, independently of interpreter-specific extraction defaults.
    for member in members:
        target = into / member.name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    # Include parent directories tar may omit from its explicit member list.
    expected: Manifest = {}
    for member in members:
        path = Path(member.name)
        expected[path.as_posix().rstrip("/")] = None if member.isdir() else member.size
        for parent in path.parents:
            if parent != Path("."):
                expected[parent.as_posix()] = None
    return expected


def _entry_manifest(root: Path, entry: Path) -> Manifest:
    paths = [entry, *entry.rglob("*")] if entry.is_dir() else [entry]
    return {p.relative_to(root).as_posix(): None if p.is_dir() else p.stat().st_size for p in paths}


def _folder_manifest(root: Path) -> Manifest:
    return {p.relative_to(root).as_posix(): None if p.is_dir() else p.stat().st_size for p in root.rglob("*")}


def _validate_document(staging: Path, uuid: str, signature: bytes) -> None:
    meta_path = staging / f"{uuid}.metadata"
    if not meta_path.is_file():
        raise ValueError(f"incomplete fetch: missing {uuid}.metadata")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("metadata is not an object")
        if meta.get("type") != "CollectionType":
            content = json.loads((staging / f"{uuid}.content").read_text(encoding="utf-8"))
            if not isinstance(content, dict):
                raise ValueError("content is not an object")
            kind = content.get("fileType")
            if kind in ("pdf", "epub") and not meta.get("deleted") and not (staging / f"{uuid}.{kind}").is_file():
                raise ValueError(f"missing base {kind}")
    except (OSError, ValueError) as exc:
        raise ValueError(f"incomplete document {uuid}: {exc}") from exc
    if local_signature(staging, uuid) != signature:
        raise ValueError(f"document {uuid} changed during fetch; retry the pull")
