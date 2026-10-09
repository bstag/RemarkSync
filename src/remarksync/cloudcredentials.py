"""Linux credentials: private files and authenticated encryption with an external key.

The server mounts its key separately from the encrypted account profile and
document archive. Protects against disclosure of the archive/profile alone;
an administrator or process with access to both key and profile can decrypt.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .cloud import CloudError


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise CloudError("Cloud credential directory must be a real directory owned by the service account.")
    path.chmod(0o700)


def read_private(path: Path) -> bytes:
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise CloudError("Cloud credential/key files must be owned by the service account with mode 0600.")
            if info.st_size > 64 * 1024:
                raise CloudError("Cloud credential/key file exceeds its size limit.")
            return stream.read(64 * 1024 + 1)
    except OSError as exc:
        raise CloudError("Could not read the private cloud credential/key file.") from exc


def protect(data: bytes, profile: Path, *, decrypt: bool = False) -> bytes:
    configured = os.environ.get("REMARKSYNC_CLOUD_KEY_FILE")
    if not configured:
        raise CloudError("Linux cloud sync needs REMARKSYNC_CLOUD_KEY_FILE pointing to its private encryption key.")
    key_path = Path(configured)
    if key_path.resolve().is_relative_to(profile.resolve()):
        raise CloudError("Mount the cloud encryption key separately from the account profile.")
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:
        raise CloudError("Install RemarkSync's server dependencies for Linux credential encryption.") from exc
    key = read_private(key_path)
    try:
        cipher = Fernet(key)
        return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
    except (ValueError, InvalidToken) as exc:
        raise CloudError("Could not unlock cloud credentials with the configured encryption key.") from exc
