"""Recoverable publication of documents into an existing flat backup folder.

The old document is copied into history before any published entry changes.
A durable journal names the old and incoming entries. An interrupted or failed
publication restores the old files; only a durable commit completes it. The
OS writer lock is released automatically when a process exits.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid as uuidlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = ".remarksync-history"
TRANSACTION_DIR = ".remarksync-transactions"
LOCK_NAME = ".remarksync-pull.lock"


def validate_id(uuid: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", uuid):
        raise ValueError(f"invalid document identity: {uuid!r}")


@contextmanager
def writer_lock(dest: Path):
    path = dest / LOCK_NAME
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another pull is already updating this backup") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another pull is already updating this backup") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _inside(root: Path, path: Path) -> Path:
    resolved, target = root.resolve(), path.resolve()
    if target == resolved or resolved not in target.parents:
        raise ValueError(f"refusing backup operation outside {root}: {path}")
    return target


def _copy(src: Path, dest: Path) -> None:
    if src.is_symlink() or (src.is_dir() and any(p.is_symlink() for p in src.rglob("*"))):
        raise ValueError(f"links are not document files: {src}")
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)


def _delete(root: Path, path: Path) -> None:
    _inside(root, path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".pending")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def recover(dest: Path) -> None:
    """Finish committed journals; roll back every interrupted publication.

    Must be called under writer_lock before planning a new pull. A recovery
    failure leaves its journal/history in place for the next attempt.
    """
    journals = dest / TRANSACTION_DIR
    if not journals.exists():
        return
    _inside(dest, journals)
    for journal in sorted(journals.glob("*.json")):
        state = json.loads(journal.read_text(encoding="utf-8"))
        uuid = state["uuid"]
        validate_id(uuid)
        names = state["old"] + state["incoming"]
        if any(Path(n).name != n or not (n == uuid or n.startswith(uuid + ".")) for n in names):
            raise ValueError("invalid document names in recovery journal")
        if not state.get("committed"):
            history = _inside(dest / HISTORY_DIR / uuid, dest / state["history"])
            # Copy old entries over their published counterparts. Remove only
            # incoming-only entries, so an interruption during rollback never
            # destroys the history needed to try it again.
            for name in state["old"]:
                src = history / name
                if not src.exists():
                    raise ValueError(f"missing recovery file: {src}")
                if (dest / name).exists():
                    _delete(dest, dest / name)
                _copy(src, dest / name)
            for name in set(state["incoming"]) - set(state["old"]):
                _delete(dest, dest / name)
        journal.unlink()
    if not any(journals.iterdir()):
        journals.rmdir()


def publish(dest: Path, staging: Path, uuid: str, entries, move, *, prune: bool = False) -> None:
    """Publish one validated document, retaining the previous raw revision."""
    validate_id(uuid)
    old, incoming = entries(dest, uuid), entries(staging, uuid) if not prune else []
    revision = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuidlib.uuid4().hex
    history = dest / HISTORY_DIR / uuid / revision
    _inside(dest, history)
    history.mkdir(parents=True)
    for path in old:
        _copy(path, history / path.name)
    write_json(history / ".revision.json", {"uuid": uuid, "acquired_at": revision.split("-")[0],
                                            "reason": "pruned" if prune else "replaced",
                                            "entries": [p.name for p in old]})
    journal = dest / TRANSACTION_DIR / f"{uuid}.json"
    _inside(dest, journal)
    journal.parent.mkdir(exist_ok=True)
    state = {"uuid": uuid, "history": history.relative_to(dest).as_posix(),
             "old": [p.name for p in old], "incoming": [p.name for p in incoming], "committed": False}
    write_json(journal, state)
    try:
        for path in old:
            _delete(dest, path)
        if not prune:
            move(staging, dest, uuid)
        state["committed"] = True
        write_json(journal, state)
    except BaseException:
        recover(dest)
        raise
    journal.unlink()
    if not any(journal.parent.iterdir()):
        journal.parent.rmdir()
