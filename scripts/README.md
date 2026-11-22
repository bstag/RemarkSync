# Supported scripts and local scratch

Reviewed 2026-10-03. Application workflows belong in `remarksync` CLI/GUI;
scripts below handle setup, independent verification, packaging and recovery.
Whole-library ZIP operations belong to the application: `remarksync backup
verify FILE.zip` and `remarksync backup restore FILE.zip NEW_DIRECTORY`; see
[backup operations](../docs/backups.md).
Run Python tools with the repository's installed `.venv` environment. None is
an automatically installed Windows service or scheduled task.

## Supported tools

| Script | Keep/status | Purpose and effects | Prerequisites |
| --- | --- | --- | --- |
| `setup-cloud-client.ps1` | Keep; Windows x64 installer | Downloads pinned rmapi v0.0.35 ZIP, verifies SHA256, writes only `.tools/rmapi`; does not pair/upload | PowerShell, HTTPS |
| `install-shortcut.ps1` | Keep; Windows convenience | Creates `RemarkSync.lnk` in the user's Start Menu and desktop, running this checkout's `.venv\Scripts\pythonw.exe -m remarksync` with no console window; `-NoDesktop` skips the desktop, `-Remove` deletes both. Installs nothing and adds no login task | PowerShell, the checkout's `.venv` |
| `setup-cloud-client.py` | Keep; Linux/container installer | Downloads amd64/arm64 pinned client and verifies SHA256; writes `.tools/rmapi`; does not pair/upload | Python on Linux or explicit `--arch` during build |
| `connect-server.ps1` | Keep; installation-specific bootstrap | Installs the already-created dedicated public key on `$REMARKSYNC_SSH_HOST` using interactive SSH password input, then verifies key authentication | Existing trusted SSH host, ignored private/public key pair; run only for initial access/recovery |
| `pair-server.py` | Keep; installation-specific re-pairing UI | Masked code sent through SSH stdin to the server worker; renews server pairing, saves only `{paired: bool}` under ignored `tmp/deploy` | PySide6, dedicated key, existing release and Docker sudo access |
| `verify-cloud-trial.py` | Keep; diagnostic | Reads a trial report and USB backup, verifies archive/PDF evidence, writes `verification.json` beside the ignored trial | Trial JSON, matching backup, PyMuPDF; mismatched revisions can legitimately differ |
| `check-repository-hygiene.py` | Keep; pre-commit/package check | Read-only ignore, known-secret and documentation-link checks; `--history` includes reachable Git blobs; prints paths/categories only | Git checkout and Python; no cloud/device access |
| `package-server.py` | Promoted from scratch; keep | Hygiene preflight, explicit source/tests/docs/deployment package plus SHA256 JSON sidecar; atomically replaces generated archive under ignored `tmp` | Python, Git; no SSH/deployment or credentials included |
| `reexport-preservation.py` | Promoted from yellow repair; keep | Offline re-render of one explicitly selected cloud revision; retains archive/old PDFs, atomically selects a new rendering | Installed RemarkSync, stopped acquisition worker, writable cloud store; no profile/key/network |

The two deployment helpers take this installation's host, key path and release
directory from `REMARKSYNC_SSH_HOST`, `REMARKSYNC_SSH_KEY` and
`REMARKSYNC_RELEASE`, and refuse to run without them. They are not general
deployment APIs: review what they do before pointing them at a machine, and do
not paste a password, token or pairing code into their source. A reviewed
deployment profile/installer is follow-up work, not an existing feature.

## Examples

```powershell
./scripts/setup-cloud-client.ps1
./scripts/install-shortcut.ps1
.venv/Scripts/python scripts/pair-server.py
.venv/Scripts/python scripts/check-repository-hygiene.py --history
.venv/Scripts/python scripts/package-server.py
.venv/Scripts/python scripts/verify-cloud-trial.py TRIAL/report.json C:/Backups/reMarkable/raw
.venv/Scripts/python scripts/reexport-preservation.py CLOUD_STORE --document UUID --revision RAW_SHA256
```

The default package is `tmp/deploy/remarksync-server.tgz` with
`remarksync-server.json` as its checksum/count sidecar. Packaging does not deploy
or restart the server. The offline repair script belongs on a verified local
cloud store or in a one-off server container with only the library volume;
desktop download stores intentionally cannot act as authoritative export stores.
See [server operations](../deploy/server/README.md) before a production repair.

## Deployment tools

| File | Keep/status | Effects |
| --- | --- | --- |
| `deploy/server/Dockerfile` | Keep | Builds the native Python renderer and checksum-pinned Linux client |
| `deploy/server/compose.yaml` | Keep | Declares dedicated sync/web services and named persistent volumes |
| `deploy/server/initialize.py` | Keep; setup-only | Sets volume ownership/private modes and creates a key only if missing; never replaces an existing key |
| `deploy/server/check-runtime.sh` | Keep; diagnostic | Starts temporary real-mount sync containers to verify ownership/session/key conditions; no cloud acquisition |
| `deploy/server/check-linux.sh` | Keep; synthetic image check | Temporary RAM dependencies/fixtures only; no real library/profile/key mounts |
| `deploy/server/check-library.py` | Keep; read-only verification | Checks current archives, raw files, PDFs, original attachments and scenes against stored hashes; optional library argument supports PC downloads |

`check-library.py` expects a completed successful state. A sync running during
the check can legitimately require a later retry. Source/file counts are
observations, not fixed assertions about the user's account size.

## Scratch disposition

The following files were used locally under ignored `tmp/deploy`; they are not
part of the supported package or normal startup. They remain local evidence,
and this housekeeping pass does not delete personal exports/settings backups.

| Scratch artifact | Disposition |
| --- | --- |
| `package-server.py` | Superseded by `scripts/package-server.py`; use the maintained source allowlist/preflight |
| `reexport-discovery-yellow.py` | Completed document-specific repair; superseded by generic `scripts/reexport-preservation.py`; do not reuse hard-coded UUID/revision |
| `verify-discovery-highlight.py` | Historical/stale verifier of earlier revisions; not suitable for current publication; use `check-library.py` |
| `configure-desktop-server.py` | Completed one-time cutover and GUI-process proof; contains fixed count assumptions and changes QSettings; do not rerun as routine startup |
| `desktop-settings-before-server.json` | Private local preference rollback evidence; keep ignored |
| `desktop-second-sync.json`, `server-pair-result.json`, logs, package JSON/TGZ | Generated diagnostic/build evidence; keep ignored, never use as account secrets |
| `tmp/pdfs` images/PDFs | Local visual QA evidence; personal documents, not repository fixtures |
| `verify-site-backup.py`, `output/site-backup` ZIP/restored store/report | Completed actual HTTPS download/offline restore proof; keep ignored; ordinary recovery uses the maintained `remarksync backup` CLI |

The actual VPS release also contains document-specific verification/repair
helpers used during validation. They are historical scratch, not a server job;
do not run them against a new revision. Useful behavior is now represented by
the supported generic tools and synthetic tests.

## Device experiments

Keep renderer/compiler oracles in `tests/real` because their independent tablet
evidence catches mistakes synthetic fixtures cannot. Several manual `upload`
modes create notebooks on the device. The firmware scene-debug experiment is
retired for live use because it produced unreadable page files; offline comparison
of captured evidence remains useful. See [the full inventory](../tests/real/README.md).
