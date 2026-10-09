# Whole-library downloads stream retained artifacts and rebuild the raw library

Status: accepted, 2026-10-04 UTC. Implemented in the private web service and
verified by downloading/restoring the real library offline.

The desktop downloader is already an off-server backup of acquired revisions.
The requested site download must additionally preserve older server-only history
in one portable file without broadening the web worker's credential access or
buffering an entire library in its 256 MiB container.

The worker captures current published metadata after a successful sync, enumerates
retained immutable archives/completed PDFs, and streams ZIP64 with allowlisted
metadata and a final SHA256 manifest. Artifact content is checked before and
during transfer. New acquisition can continue after capture because it does not
overwrite the selected immutable source files. Interrupted/changed-source streams
lack the completed backup manifest/footer and must fail offline verification.
One backup runs per process; individual downloads/status remain available.

The mutable raw projection, journals, caches, account profile/key and deployment
credentials are excluded. Offline restoration verifies every retained archive
and reconstructs current raw files in a new staging directory before publishing
it. Existing libraries are refused. This avoids a mixed raw-file snapshot during
acquisition and retains the web container's read-only data mount.

The tradeoffs are no range-resume or upfront download size, pre-transfer checksum
work, explicit file/size/metadata limits, and separate replacement-server/account
recovery. Credential-free document restoration is proven; it does not establish
a complete unattended disaster-recovery process or full rendering fidelity.
See [backup operations](../backups.md) for limits and recovery commands.
