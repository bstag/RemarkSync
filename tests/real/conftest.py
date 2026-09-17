"""Opt-in fixtures for validating against real data.

These tests need two things this repo does not ship: a raw backup of a real
tablet, and the reMarkable desktop app's own store of the same library.
Point them at both and the suite runs; leave either unset and it skips, so
the synthetic suite stays hermetic.

    $env:REMARKSYNC_REAL_BACKUP   = "C:\\Backups\\reMarkable\\raw"
    $env:REMARKSYNC_DESKTOP_STORE = "$env:APPDATA\\remarkable\\desktop"
    .venv\\Scripts\\python -m pytest tests/real -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("PySide6")

ENV_BACKUP = "REMARKSYNC_REAL_BACKUP"
ENV_STORE = "REMARKSYNC_DESKTOP_STORE"


def _directory_from_env(name: str) -> Path:
    raw = os.environ.get(name)
    if not raw:
        pytest.skip(f"{name} is not set; real-data validation skipped")
    path = Path(raw)
    if not path.is_dir():
        pytest.skip(f"{name}={raw!r} is not a directory")
    return path


@pytest.fixture(scope="session")
def real_backup() -> Path:
    """A raw xochitl backup of an actual tablet. Only ever read."""
    return _directory_from_env(ENV_BACKUP)


@pytest.fixture(scope="session")
def desktop_store() -> Path:
    """The reMarkable desktop app's synced store. Only ever read."""
    return _directory_from_env(ENV_STORE)


@pytest.fixture(scope="session")
def pairings(real_backup: Path, desktop_store: Path):
    from .oracle import pair_pages

    pairs = pair_pages(real_backup, desktop_store)
    if not pairs:
        pytest.skip("no page in the backup has a desktop-app render to compare against")
    return pairs
