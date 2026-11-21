"""Where the real-data helpers read from and keep their work.

One place for the defaults every manual experiment used to spell out for
itself. Each is overridden by an environment variable (tests/real/README.md
lists them); the defaults are the machine the measurements were made on.
Nothing here touches the tablet or creates a directory.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

# A raw xochitl backup of an actual tablet. Only ever read.
RAW = Path(os.environ.get("REMARKSYNC_REAL_BACKUP", r"C:\Backups\reMarkable\raw"))
# The reMarkable desktop app's synced store. Only ever read.
STORE = Path(os.environ.get("REMARKSYNC_DESKTOP_STORE",
                            str(Path(os.environ.get("APPDATA", str(Path.home()))) / "remarkable" / "desktop")))
# The tablet's USB web interface.
HOST = os.environ.get("REMARKSYNC_TABLET", "10.11.99.1")


def work_dir(variable: str, default_name: str) -> Path:
    """A private work directory: ``variable`` if set, else a folder under TEMP."""
    temp = os.environ.get("TEMP") or tempfile.gettempdir()
    return Path(os.environ.get(variable, str(Path(temp) / default_name)))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
