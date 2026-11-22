"""Where the desktop app writes what went wrong.

Started from its shortcut the app has no console: ``pythonw`` gives it no
stderr, so a logged warning or an uncaught error would otherwise leave no
trace anywhere. The window logs to ``remarksync.log`` (rotated); the worker
processes append to ``workers.log``, a file of their own so that two
processes never rotate one file under each other.

The folder is the user's local application data, not the checkout: a log is
this PC's business and has to work wherever the program is installed.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / "RemarkSync" / "logs"


def configure(name: str = "remarksync", *, rotate: bool = True, folder: Path | None = None) -> Path | None:
    """Send warnings and worse to ``<name>.log``. Returns the file, or None
    when the folder cannot be used; the program runs the same without a log."""
    folder = folder or log_dir()
    path = folder / f"{name}.log"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if rotate:
            handler: logging.Handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        else:
            handler = logging.FileHandler(path, encoding="utf-8", delay=True)  # no file until something is logged
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    return path


def where_it_failed(exc: BaseException) -> str:
    """The error's type and the lines it passed through, without its message.

    For the cloud worker: an exception's text can quote what the service or
    the client said, and that does not belong in a file. Which line failed is
    enough to find the fault.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    trail = " <- ".join(f"{Path(frame.filename).name}:{frame.lineno} {frame.name}" for frame in reversed(frames))
    return f"{type(exc).__name__} at {trail}"


def log_uncaught(on_report=None) -> None:
    """Log exceptions nothing caught (Qt slots included) instead of losing
    them. ``on_report(summary)`` is told too; it may be called from any thread."""
    log = logging.getLogger("remarksync")
    previous = sys.excepthook

    def hook(kind, value, trace):
        log.error("uncaught error", exc_info=(kind, value, trace))
        if on_report is not None:
            try:
                on_report(f"{kind.__name__}: {value}")
            except Exception:  # reporting must never raise from the hook
                pass
        if sys.stderr is not None:
            previous(kind, value, trace)

    sys.excepthook = hook
