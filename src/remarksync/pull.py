"""Pull a raw backup from a reMarkable tablet over SSH (USB or Wi-Fi),
copying only what changed.

The tablet exposes its document store at ``root@10.11.99.1`` (USB) or its
Wi-Fi IP; the password is the one shown in Settings → Help → Copyrights
and licences unless a key was installed. It ships BusyBox, not rsync, so
the pull is built from ``ssh`` and ``tar``:

1. one ``tar`` over SSH brings every ``.metadata`` file (small, one per
   document);
2. each is compared with the local copy: the tablet rewrites a document's
   metadata on every edit, so a changed or missing file marks a document
   to fetch;
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

    def read_metadata(self) -> dict[str, bytes]:
        """``.metadata`` file contents by document uuid, for every document."""

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

    def read_metadata(self) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(self._tar(["*.metadata"])), mode="r:") as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith(".metadata"):
                    handle = tar.extractfile(member)
                    if handle is not None:
                        out[Path(member.name).name[: -len(".metadata")]] = handle.read()
        return out

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

    def read_metadata(self) -> dict[str, bytes]:
        return {p.name[: -len(".metadata")]: p.read_bytes() for p in self.root.glob("*.metadata")}

    def fetch(self, uuids: list[str], into: Path) -> None:
        for u in uuids:
            for src in self.root.glob(f"{u}.*"):
                shutil.copy2(src, into / src.name)
            if (self.root / u).is_dir():
                shutil.copytree(self.root / u, into / u, dirs_exist_ok=True)


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


def plan(remote: dict[str, bytes], dest: Path) -> PullReport:
    """Which documents to fetch, given the tablet's metadata and the local copies."""
    report = PullReport()
    for uuid, data in remote.items():
        local = dest / f"{uuid}.metadata"
        if not local.exists():
            report.new.append(uuid)
        elif local.read_bytes() != data:
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

    remote = tablet.read_metadata()
    report = plan(remote, dest)
    todo = list(remote) if force else report.new + report.changed
    if force:
        report.changed, report.new, report.unchanged = [u for u in remote if u not in report.new], report.new, []
    if dry_run:
        return report

    for start in range(0, len(todo), FETCH_BATCH):
        batch = todo[start : start + FETCH_BATCH]
        for uuid in batch:
            _remove_document(dest, uuid)  # so pages removed on the tablet vanish here too
        tablet.fetch(batch, dest)
        for uuid in batch:
            if progress is not None and progress(uuid) is False:
                report.completed = False
                return report

    if prune:
        for uuid in report.removed:
            _remove_document(dest, uuid)
    return report


def _remove_document(dest: Path, uuid: str) -> None:
    for p in dest.glob(f"{uuid}.*"):
        p.unlink()
    if (dest / uuid).is_dir():
        shutil.rmtree(dest / uuid, ignore_errors=True)


def _safe_extract(tar: tarfile.TarFile, into: Path) -> None:
    """Extract, refusing paths that would escape ``into``."""
    into = into.resolve()
    for member in tar.getmembers():
        target = (into / member.name).resolve()
        if into not in target.parents and target != into:
            raise RuntimeError(f"refusing to extract {member.name!r} outside {into}")
    tar.extractall(into)
