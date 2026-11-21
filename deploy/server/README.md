# Ubuntu VPS deployment through Tailscale

This package runs whole-library reMarkable Cloud acquisition and original-PDF
preservation while the desktop PC is off. The browser library serves current
preservation PDFs and raw `.rmdoc` archives, including notebooks. The desktop
can download this library automatically; notebook PDF generation is a follow-up.
Earlier revisions remain in the persistent library volume.

Reviewed 2026-10-03. See [current status](../../docs/current-status.md) for the
selected installation and latest verification, [settings](../../docs/settings.md)
for configuration, and [script inventory](../../scripts/README.md) for supported
tools. Cloud uploads are [proposed](../../docs/spec/server-uploads.md); the current
deployment downloads and exports documents.

## Selected machine and access

The deployed machine is an Intel x86-64 VPS with 4 vCPUs, 8 GB RAM and 60 GB
storage. Its SSH target comes from `REMARKSYNC_SSH_HOST` and is reached over
Tailscale DNS; set that variable to your own `user@host`. SSH host trust and the
dedicated deployment key are configured per installation. The paired sync worker and private browser
library are deployed and running as described below.

For initial access or key recovery, run `scripts/connect-server.ps1` on the
Windows PC in an interactive terminal. This is already completed for the
deployed server; normal verification does not reinstall the key. SSH prompts for the password
itself. The script installs the public
half of the private deployment key from ignored `.tools/deploy-ssh/`.
The key's `restrict` option disables forwarding, agent forwarding and PTY;
ordinary noninteractive deployment commands still work. The script does not
modify password authentication or replace other authorized keys. It requires
the existing known-host record; it never accepts an unknown key automatically.

Before deployment, inspect Ubuntu/architecture, available disk, Docker and
Compose versions, the deployment user's Docker access, and existing listeners/containers.
Use a new directory and the dedicated `remarksync` Compose project; do not
overwrite another application's files, volumes or Tailscale Serve endpoint.

## Build, initialize and pair

From the PC checkout, prepare a source-only release with a hygiene preflight:

```powershell
.venv/Scripts/python scripts/check-repository-hygiene.py --history
.venv/Scripts/python scripts/package-server.py
```

The archive and checksum/count sidecar are under ignored `tmp/deploy`. The
packager intersects its source allowlist with versionable Git paths; ignored
scratch, credentials and personal documents stay out. Packaging does not transfer
or deploy the archive. Docker uses a separate default-deny build context.
See [data handling](../../docs/security-and-data.md) for check limits.

The following setup commands are for a new installation or deliberate recovery;
do not re-pair a healthy worker during routine checks. From a new copy of this
source on the Ubuntu server:

```bash
docker compose -f deploy/server/compose.yaml build
docker compose -f deploy/server/compose.yaml --profile setup run --rm initialize
sh deploy/server/check-runtime.sh
docker compose -f deploy/server/compose.yaml run --rm sync cloud pair
```

The last command is interactive. Open
[reMarkable's pairing page](https://my.remarkable.com/device/browser/connect)
on the PC, generate a fresh eight-character code, and enter it at the masked
terminal prompt. The code is not passed in command arguments or environment
variables. The Windows pairing remains on Windows; the server is paired
independently.

The runtime check uses this server's noninteractive `sudo docker` access. It
opens two fresh sync containers without contacting reMarkable, and verifies
volume ownership, private RAM-session permissions and the existing key.

The image downloads rmapi v0.0.35 from its pinned upstream release and checks
the archive SHA256. Linux amd64:
`117616151d11937446ead6972b0934f97155443087f8406141db38fd1ac8fb25`.
Linux arm64, for another VPS:
`645c170d8119b4dcb652cf79612e362fcb032dc0e5869ff520eec1324da39637`.
Only pairing and fresh listings are delegated to this client; cloud document
downloads remain size/hash-verified Python GETs. No cloud document writes.
[Pinned release metadata](https://api.github.com/repos/ddvk/rmapi/releases/tags/v0.0.35).

## Start and verify

```bash
docker compose -f deploy/server/compose.yaml up -d sync web
docker compose -f deploy/server/compose.yaml logs --tail 30 sync
curl --fail http://127.0.0.1:8787/status
```

After the status becomes `ok`, verify all current archived/source checksums
and embedded original PDF/scene attachments without contacting the cloud:

```bash
sudo -n docker exec -i remarksync-web-1 python - < deploy/server/check-library.py
```

The verifier reads only the library volume; the web container has no account
credentials. It also runs locally against an existing downloaded store:
`python deploy/server/check-library.py output/cloud-sync`.

The first sync downloads the whole library and generates PDFs where a base
PDF exists. Notebook raw archives are preserved even though notebook PDF
generation is not implemented. A successful status includes document/PDF
counts and `last_success`. Failures retain prior revisions and back off.
Verify a second sync reuses unchanged revisions, restart recovery, and an
actual tablet edit before treating the hosted job as the primary source.

After confirming an unused Tailscale Serve port, a private HTTPS endpoint
can proxy the localhost service. For example, if HTTPS port 8443 is unused:

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8787
tailscale serve status
```

Use the URL that Serve reports. Tailnet access rules apply; grant only the
intended users/devices access to that port. Serve is the intended private
endpoint. Public Funnel publication is outside this deployment.
[Serve](https://tailscale.com/docs/features/tailscale-serve),
[access controls](https://tailscale.com/docs/features/access-control/acls).

## Data, credentials and recovery

Named volumes are `remarksync_library`, `remarksync_cloud-profile` and
`remarksync_cloud-key`. Runtime processes use UID/GID 10001. Initialization
sets volume directory ownership/0700 and creates a 0600 encryption key only
when absent. An existing key is never rotated or replaced by initialization.
All volume mounts disable Docker's automatic image-file copy with `nocopy`.
Without it, mounting an empty initialized volume can reset its ownership to
the image directory's root account, preventing the service from pairing or
writing its first library. Initialization sets permissions; subsequent mounts
preserve them, including the web container's read-only library mount.

Linux tokens are Fernet-encrypted in the account profile using the separately
mounted `/keys/cloud.key`. Key and profile files must be private regular files
owned by the service account. Credentials are not in the image or library.
The sync container's transient plaintext token/cache session is in a private
RAM filesystem, removed after an operation and cleared when the container
stops. The web container mounts only the library, read-only, and has no key
or credential profile. Its only host port is `127.0.0.1:8787`; the VPS public
IPv4 address does not expose that port. Artifact routes are restricted to
published UUID/revision references, verify checksums, support PDF byte ranges,
and refuse arbitrary paths, symlinks and corrupt archives.

An administrator with access to both key and profile can decrypt credentials.
Desktop downloads already back up acquired documents/PDFs to a second machine.
For coverage of all older server-only revisions/state, separately replicate the
library volume to another machine or private object storage if required. Credential
recovery requires both encrypted profile and key; keep the key as a separate
secret backup, or pair again after a loss. Do not delete named volumes to stop
the service. Use `docker compose ... stop`, or `down` without `--volumes`.
Old document revisions are retained indefinitely; monitor disk growth before
choosing a retention policy. The whole-library ZIP covers retained server history
and has a verified real restore. Complete replacement-service/account recovery
and a backup cadence/destination policy remain follow-up work.

## Desktop downloads

The private site's **Download whole-library backup** link serves `/backup.zip`.
It streams retained raw revisions and completed preservation PDFs with checksum
metadata and offline restoration instructions. The web volume remains read-only;
only one backup runs per web process, and no credentials are included. Start
after a completed sync. Use `remarksync backup verify` and `backup restore` on
the downloaded ZIP. See [backup operations](../../docs/backups.md) for limits,
recovery and the distinction from automatic desktop downloads.

The read-only `/manifest` endpoint publishes current document identifiers,
raw revision/file hashes, archive/PDF hashes, names and export warnings.
It excludes local paths, account credentials and internal sync reports. It
uses the same private Tailscale HTTPS endpoint as the browser library.

On the PC, choose **Library sync… → Central preservation server**, then enable
automatic checks. The client downloads archives and existing PDFs, verifies
SHA256 and the exact extracted file set, and publishes a separate raw library
with recoverable history. Network destinations require HTTPS; redirects are
refused. Failed downloads and absent server documents leave earlier copies
available offline. Same-revision PDF corrections retain earlier renderings.
The source address is bound to the local store to prevent accidental mixing.

`remarksync server-sync sync` performs one download pass; `server-sync watch`
runs a foreground polling process. The GUI resumes five-minute checks when
RemarkSync opens; it does not create a Windows service or login task.
The actual PC download verified 44 documents, 10 folders, 54 archives, 378 source
files, 12 PDFs/original attachments and 137 scene attachments. The next CLI
and GUI child-process passes reused all 54 entries without downloads.
The updated web image was deployed without stopping cloud acquisition;
the prior image is retained as `remarksync-server:before-central-download-20261003`.

Since 2026-11-21 both services run image `bc4104ff6da8` from release
`~/remarksync-preservation/releases/20261121-review-01` (earlier that day:
`9007e0e2b60e` from `20261121-removed-01`, kept as
`remarksync-server:before-review-cleanup-20261121`); see
[current status](../../docs/current-status.md). Before that the sync image was
`bc72ae4c437f` and the web image `320211395287`. Recreate sync only straight
after a completed pass.
The backup feature updated only web from release
`~/remarksync-preservation/releases/20261004-backup-01`; the original
acquisition/pairing release remains in place. The previous web image is tagged
`remarksync-server:before-whole-library-backup-20261004` for rollback.

The 2026-10-04 UTC backup image passed **139 Linux tests, two Windows-only skips**;
the complete Windows suite passed **299 tests, 19 skips**. A real private HTTPS
ZIP download (129,570,275 bytes) verified and restored 45 documents, 10 folders,
60 retained revisions and 18 PDFs. Hosted/restored checks both verified 55 current
archives, 383 raw files, 13 original-PDF attachments and 138 scene attachments.
The browser button and existing desktop manifest also passed live checks.

## Validation

Application baseline before repository housekeeping: 286 passed, 17 skipped
on Windows. Latest checkout checks are recorded in
[current status](../../docs/current-status.md). Private HTTP
tests cover index/status, PDF range/HEAD requests, notebook archives, unsafe
paths, unpublished revisions, corrupt artifacts and symlinks. Credential
tests cover encryption, wrong keys and required key separation; POSIX-only
ownership, pairing and RAM-session tests now pass on Linux. The masked remote
pairing dialog has four passing transport/completion tests on Windows. A
completion-handler name collided with QDialog's built-in `finished` signal,
leaving the window stuck on "Pairing" after SSH exited; the handler now has a
distinct name, and tests cover successful pairing and worker/SSH failure.
The amd64
installer downloaded and checksum-verified the real Linux release without
executing it on Windows. The browser library was visually inspected against
the actual downloaded cloud store.

The initial 2026-10-03 image built successfully on the server (Ubuntu 24.04.4,
x86-64), and its synthetic Linux container suite had **129 passed, 2 Windows-only skips**,
including scene decoding and PDF export tests against the installed image.
The initial disposable test install selected an incompatible `packaging`
version; the test bootstrap now honors rmscene's `packaging>=23,<24` constraint
and the clean rerun passes. The real service's installed client path and
private key mount were verified separately. Local WSL could not start, so
Linux verification used the actual target server.

Release directory: `~/remarksync-preservation/releases/20261003-linux-01`.
The deployment user has existing noninteractive sudo access to Docker; no group membership
or sudo policy was changed. Existing volumes and the chosen directory were
absent before deployment. The VPS had 4.9 GB available RAM and 28 GB free disk.
Only dedicated RemarkSync volumes, image/network and sync/web containers were
created. The existing applications and listeners were retained. Runtime
containers opt out of the existing Watchtower image updater.

The web service is reachable privately through Tailscale Serve at
`https://server.tailnet.ts.net:8443/`; its `/status` was verified from
the Windows PC using normal HTTPS certificate validation. Serve previously
had no endpoints, and ports 8787/8443 were free. No public Funnel endpoint was
enabled. A masked local pairing window sent the human-entered code through SSH
stdin to a one-off Docker worker; the encrypted server profile is service-owned
with mode 0600. The sync worker runs every 300 seconds, and both services use
`unless-stopped` restart policies. The existing Docker system service is enabled.

The first hosted pass completed at 2026-10-03 21:59 UTC: **44 documents,
10 folders, 54 immutable archives and 12 preservation PDFs**, with zero
acquisition/export failures and zero cloud document writes. Read-only
verification checked **377 raw files, all 12 embedded original PDF attachments
and 136 scene attachments** against their cloud-indexed hashes. The complete
library occupied 182,815,351 bytes (about 174 MiB); each idle service used about
41 MiB RAM. The browser page and real PDF HEAD/range and notebook archive
responses were checked over the private HTTPS endpoint. A PDF downloaded
through that endpoint matched its server checksum; Poppler inspection of the
Life Group manual's page 2 and appended context page 57 confirmed highlights,
handwriting and the complete overflow note remain visible.

Restarting both RemarkSync services retained the account and all revisions.
The immediate subsequent pass completed at 22:01 UTC with **54 unchanged,
zero downloads, zero changes and zero acquisition/export failures**.

A subsequent live tablet edit on Discovery Seminar page 2 reached reMarkable
Cloud, then the unforced five-minute poll at 22:11 UTC acquired it. The pass
completed at 22:11:53 UTC with **1 changed, 53 unchanged and zero failures**.
Its page-2 preservation manifest contains five new highlight groups; the
original PDF hash is unchanged and the previous PDF revision remains stored.
Full current-library verification now checks 378 source files and 137 scene
attachments. The downloaded updated PDF matches its server checksum, and
Poppler inspection confirms the highlights are visible and aligned. The open
browser library was refreshed to publish the updated link.

The user confirmed those five highlights were yellow on the tablet. Their
palette is `YELLOW` but their explicit RGBA is `(0, 0, 0, 255)`. The decoder now
treats only that exact pair as a yellow placeholder; custom RGBA colors and
partially transparent black keep their existing interpretation. Regression
tests reproduce the live encoding and verify yellow PDF pixels.

The latest Discovery Seminar raw revision is
`c63a84a8c840ee7fd8ae0500c3d700a6cd4202bd3a11d883c48e5196c1ad1bc8`.
Its corrected PDF checksum is
`a7ff27b348456bc204a8d5f11f7f275d823d4bf759ff9f6f90fe70a22bcc63c1`.
The private HTTPS download matches that checksum, and Poppler inspection
confirms all five highlights are yellow and aligned. The next sync retained
the corrected rendering with 54 unchanged revisions and zero failures.
Full library verification passes for 378 source files, all 12 original PDF
attachments and 137 scene attachments. Discovery Seminar page 2 still carries
a newer-format decoder warning; its exact scene bytes remain preserved.

### Regenerating a cached preservation PDF

`remarksync.cloudsync.reexport_pdf(library_path, document_uuid, expected_revision)`
regenerates a PDF offline from its verified archive while holding the library
writer lock. Stop the sync worker first and use the currently published raw
revision as the expected revision. The function refuses stale revisions and
invalid archive/PDF checksums. It needs only the library volume, without cloud
credentials or network access.

The supported wrapper is `scripts/reexport-preservation.py`. For an intentional
server repair, stop sync, choose the current UUID/raw hash from verified state,
and run the script with only the writable library volume. For example, from a
release containing that script:

```bash
sudo -n docker compose -f deploy/server/compose.yaml stop sync
sudo -n docker run --rm --network none --user 10001:10001 \
  --mount type=volume,source=remarksync_library,target=/data/library,volume-nocopy \
  --entrypoint python remarksync-server:local - /data/library \
  --document UUID --revision RAW_SHA256 < scripts/reexport-preservation.py
sudo -n docker exec -i remarksync-web-1 python - < deploy/server/check-library.py
sudo -n docker compose -f deploy/server/compose.yaml start sync
```

Replace both placeholders; this is a repair recipe, not a periodic job. Restart
acquisition even if verification reports a problem, then investigate with the
retained prior rendering. This does not mount the cloud profile or key and
does not rewrite desktop download stores.

New renderings are retained under `renderings/<pdf-sha256>/annotated.pdf` inside
the raw revision directory. The earlier `annotated.pdf` remains intact, and an
atomic state update selects the new rendering. The stable browser PDF route
serves the selected file. Existing legacy PDFs continue to work. Failure before
the state update leaves the previously published PDF readable. The yellow
repair used this path and retained the earlier gray export.

Existing rendering limits remain: decoder warnings on 78 Bible,
4 handwriting-workbook and 1 reManager guide pages, substitute typed-note fonts
and fixed-scale zoom/pan mapping are recorded in preservation manifests. Raw
annotations remain preserved; these checks do not establish full fidelity for
unsupported scene fields. Desktop downloads already provide an off-server
document/PDF backup. The whole-library ZIP additionally covers retained server
history and passed a real restore drill; full service/account recovery remains pending.

The first pairing attempt exposed Docker's empty-volume ownership reset on
the credential and library volumes. A disposable volume reproduced the reset
with default mounts and preserved ownership with `nocopy`. The corrected
Compose mounts are deployed; initialization restored ownership and verified
the encryption key was unchanged. Both fresh-container runtime checks pass.
