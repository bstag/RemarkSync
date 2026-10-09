#!/bin/sh
# Verify actual Compose volume mounts before pairing, without cloud access.
set -eu
for task_check in 1 2; do
    sudo -n docker compose -f deploy/server/compose.yaml run --rm --no-deps -T --entrypoint python sync -c '
import os
from pathlib import Path
from remarksync.cloud import CloudClient
from remarksync.cloudcredentials import private_directory
client = CloudClient()
private_directory(client.profile)
private_directory(Path(os.environ["REMARKSYNC_CLOUD_SESSION_DIR"]))
library = Path("/data/library")
assert library.is_dir() and library.stat().st_uid == os.getuid() and os.access(library, os.W_OK), "Library volume must be service-owned and writable"
assert client.executable.is_file(), "Cloud client is missing"
client._protect(b"runtime-key-check")
print("Runtime volume ownership, private session and encryption key verified.")
'
done
