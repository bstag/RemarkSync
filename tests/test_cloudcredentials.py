import os
from pathlib import Path

import pytest

from remarksync import cloud, cloudcredentials
from .test_cloud import source as cloud_source

Fernet = pytest.importorskip("cryptography.fernet").Fernet


@pytest.fixture
def source():
    return cloud_source.__wrapped__()


def test_encrypted_linux_tokens_require_external_key_and_reject_corruption(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("REMARKSYNC_CLOUD_KEY_FILE", str(key))
    # Exercise the cipher on Windows too; actual POSIX ownership checks follow below.
    monkeypatch.setattr(cloudcredentials, "read_private", lambda path: path.read_bytes())
    tokens = b"devicetoken: device\nusertoken: user\n"
    encrypted = cloudcredentials.protect(tokens, profile)
    assert tokens not in encrypted and cloudcredentials.protect(encrypted, profile, decrypt=True) == tokens
    key.write_bytes(Fernet.generate_key())
    with pytest.raises(cloud.CloudError, match="unlock"):
        cloudcredentials.protect(encrypted, profile, decrypt=True)
    with pytest.raises(cloud.CloudError, match="unlock"):
        cloudcredentials.protect(b"damaged", profile, decrypt=True)


def test_linux_key_not_silently_created_or_stored_with_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("REMARKSYNC_CLOUD_KEY_FILE", raising=False)
    with pytest.raises(cloud.CloudError, match="KEY_FILE"):
        cloudcredentials.protect(b"tokens", tmp_path)
    monkeypatch.setenv("REMARKSYNC_CLOUD_KEY_FILE", str(tmp_path / "cloud.key"))
    with pytest.raises(cloud.CloudError, match="separately"):
        cloudcredentials.protect(b"tokens", tmp_path)
    assert not (tmp_path / "cloud.key").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and permissions")
def test_linux_private_file_permissions_links_and_directory(tmp_path):
    directory = tmp_path / "private"
    cloudcredentials.private_directory(directory)
    assert directory.stat().st_mode & 0o777 == 0o700
    key = directory / "key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    assert cloudcredentials.read_private(key) == key.read_bytes()
    key.chmod(0o644)
    with pytest.raises(cloud.CloudError, match="0600"):
        cloudcredentials.read_private(key)
    link = directory / "link"
    link.symlink_to(key)
    with pytest.raises(cloud.CloudError):
        cloudcredentials.read_private(link)
    link_dir = tmp_path / "link-dir"
    link_dir.symlink_to(directory, target_is_directory=True)
    with pytest.raises(cloud.CloudError):
        cloudcredentials.private_directory(link_dir)


@pytest.mark.skipif(os.name == "nt", reason="Linux subprocess and encrypted profile")
def test_linux_pairing_cache_isolation_and_ram_session(tmp_path, source, monkeypatch):
    import json
    import subprocess
    entry, _blobs = source
    executable = tmp_path / "rmapi"
    executable.touch()
    key = tmp_path / "cloud.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    sessions = tmp_path / "ram"
    monkeypatch.setenv("REMARKSYNC_CLOUD_KEY_FILE", str(key))
    monkeypatch.setenv("REMARKSYNC_CLOUD_SESSION_DIR", str(sessions))
    client = cloud.CloudClient(executable, tmp_path / "profile")
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        work = Path(kwargs["cwd"])
        assert work.parent == sessions and kwargs["env"]["XDG_CACHE_HOME"] == str(work)
        assert not any(key.startswith("REMARKSYNC_") for key in kwargs["env"])
        config = work / "tokens.yaml"
        config.write_text("devicetoken: fake-device\nusertoken: fake-user\n")
        config.chmod(0o600)
        (work / "rmapi").mkdir()
        (work / "rmapi" / "tree.cache").write_text(json.dumps({
            "CacheVersion": 3, "SchemaVersion": "4", "Hash": "f" * 64, "Docs": [entry]}))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps([{"id": entry["DocumentID"], "type": "DocumentType"}]).encode())

    monkeypatch.setattr(cloud.subprocess, "run", fake_run)
    assert client.list("abcd1234")[0]["id"] == entry["DocumentID"]
    assert client.secret.name == "account.fernet" and b"fake-device" not in client.secret.read_bytes()
    assert client.secret.stat().st_mode & 0o777 == 0o600
    assert client.list()[0]["id"] == entry["DocumentID"]
    assert len(commands) == 2 and not list(sessions.iterdir())
    assert not list(client.profile.glob(".account-*"))
