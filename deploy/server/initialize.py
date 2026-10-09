"""Initialize service-owned named volumes; never replace an existing key."""
import base64
import os
from pathlib import Path
import stat

for name in ("/data/library", "/state/cloud", "/keys"):
    path = Path(name)
    if path.is_symlink() or not path.is_dir():
        raise SystemExit("Expected real deployment volume directories.")
    os.chown(path, 10001, 10001)
    path.chmod(0o700)

key = Path("/keys/cloud.key")
if not key.exists() and not key.is_symlink():
    with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(base64.urlsafe_b64encode(os.urandom(32)))
        stream.flush()
        os.fsync(stream.fileno())
info = key.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_size != 44:
    raise SystemExit("Existing encryption key is invalid; retained for investigation.")
os.chown(key, 10001, 10001)
key.chmod(0o600)
print("Persistent volumes initialized; existing encryption key retained.")
