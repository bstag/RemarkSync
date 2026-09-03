"""Pull a raw backup from a reMarkable tablet over SSH (USB or Wi-Fi).

Uses the system ssh/scp (Windows 10+ ships OpenSSH). The tablet exposes the
filesystem at ``root@10.11.99.1`` (USB) or its Wi-Fi IP; the password is the
one shown in Settings → Help → Copyrights and licences unless a key was
installed.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_HOST = "root@10.11.99.1"
REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl/"


def pull(dest: Path, host: str = DEFAULT_HOST, timeout: int = 600) -> Path:
    """rsync-style pull of the xochitl directory into ``dest`` (recursive,
    idempotent — re-running refreshes changed files only is NOT guaranteed
    with scp; it overwrites). Returns the destination directory."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    remote = f"{host}:{REMOTE_DIR}"
    cmd = ["scp", "-r", "-o", "StrictHostKeyChecking=accept-new", remote, str(dest)]
    log.info("pulling %s -> %s", remote, dest)
    proc = subprocess.run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"scp failed with code {proc.returncode} — is the tablet connected and SSH enabled?")
    # scp -r copies the dir itself: dest/xochitl/*. Normalise to dest/*.
    nested = dest / "xochitl"
    if nested.is_dir():
        for item in nested.iterdir():
            target = dest / item.name
            if target.exists():
                if target.is_dir():
                    import shutil

                    shutil.rmtree(target)
                else:
                    target.unlink()
            item.rename(target)
        nested.rmdir()
    return dest
