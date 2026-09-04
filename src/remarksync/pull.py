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

import io
import logging
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)

DEFAULT_HOST = "root@10.11.99.1"
REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl/"
FETCH_BATCH = 40  # documents per tar invocation (keeps the command line short)

# progress(doc_uuid) -> keep going? Returning False stops the pull.
Progress = Callable[[str], bool | None]


class Tablet(Protocol):
    """Where a backup comes from: the device's document store."""

    def read_signatures(self) -> dict[str, bytes]:
        """A signature per document uuid: its ``.metadata`` plus any text
        conversions, so either kind of change is noticed."""

    def fetch(self, uuids: list[str], into: Path) -> None:
        """Copy ``<uuid>.*`` and ``<uuid>/`` for each uuid into ``into``."""


class SshTablet:
    """The real tablet, reached with the system ssh and its BusyBox tar."""

    def __init__(self, host: str = DEFAULT_HOST, remote_dir: str = REMOTE_DIR, timeout: int = 600):
        self.host = host
        self.remote_dir = remote_dir
        self.timeout = timeout

    def _tar(self, patterns: list[str]) -> bytes:
        remote = f"cd {self.remote_dir} && tar -cf - " + " ".join(patterns)
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", self.host, remote]
        log.info("ssh %s: %s", self.host, remote[:120])
        proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ssh/tar failed with code {proc.returncode} — is the tablet connected and SSH enabled? "
                f"{proc.stderr.decode(errors='replace').strip()[:200]}"
            )
        return proc.stdout

    def read_signatures(self) -> dict[str, bytes]:
        files: dict[str, dict[str, bytes]] = {}
        data = self._tar(["*.metadata", "$(ls -d *.textconversion 2>/dev/null)"])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.lstrip("./")
                uuid = name.split("/", 1)[0].split(".", 1)[0]
                handle = tar.extractfile(member)
                if handle is not None:
                    files.setdefault(uuid, {})[name] = handle.read()
        return {uuid: _signature(parts) for uuid, parts in files.items() if f"{uuid}.metadata" in parts}

    def fetch(self, uuids: list[str], into: Path) -> None:
        for start in range(0, len(uuids), FETCH_BATCH):
            batch = uuids[start : start + FETCH_BATCH]
            patterns = [f"{u}.*" for u in batch] + [u for u in batch]
            # a document without a page directory makes tar report an error
            # but still emit the rest; tolerate that by listing existing names
            data = self._tar([f"$(ls -d {p} 2>/dev/null)" for p in patterns])
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
                _safe_extract(tar, into)


class LocalTablet:
    """A directory laid out like the tablet's store; the test adapter, and
    handy for copying between two local mirrors."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read_signatures(self) -> dict[str, bytes]:
        return {p.name[: -len(".metadata")]: local_signature(self.root, p.name[: -len(".metadata")]) for p in self.root.glob("*.metadata")}

    def fetch(self, uuids: list[str], into: Path) -> None:
        for u in uuids:
            for src in _document_entries(self.root, u):
                if src.is_dir():
                    shutil.copytree(src, into / src.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, into / src.name)


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
    dest.mkdir(parents=True, exist_ok=True)
    tablet = tablet or SshTablet(host, timeout=timeout)

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
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            tablet.fetch(batch, staging)
            for uuid in batch:
                _remove_document(dest, uuid)
                _move_document(staging, dest, uuid)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for uuid in batch:
            if progress is not None and progress(uuid) is False:
                report.completed = False
                return report

    if prune:
        for uuid in report.removed:
            _remove_document(dest, uuid)
    return report


STAGING_DIR = ".remarksync-pull-tmp"


def _document_entries(root: Path, uuid: str) -> list[Path]:
    """Everything belonging to a document: ``<uuid>.*`` (files, and the
    ``.highlights`` and similar directories) plus the ``<uuid>/`` page dir."""
    entries = list(root.glob(f"{uuid}.*"))
    if (root / uuid).is_dir():
        entries.append(root / uuid)
    return entries


def _remove_document(dest: Path, uuid: str) -> None:
    for p in _document_entries(dest, uuid):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink()


def _move_document(src: Path, dest: Path, uuid: str) -> None:
    for p in _document_entries(src, uuid):
        shutil.move(str(p), str(dest / p.name))


def _safe_extract(tar: tarfile.TarFile, into: Path) -> None:
    """Extract, refusing paths that would escape ``into``."""
    into = into.resolve()
    for member in tar.getmembers():
        target = (into / member.name).resolve()
        if into not in target.parents and target != into:
            raise RuntimeError(f"refusing to extract {member.name!r} outside {into}")
    tar.extractall(into)
