# Settings and configuration

Updated 2026-10-03. Defaults below describe source code; the selected settings
describe this installation. Account credentials are not QSettings preferences.

## Desktop preferences

Qt stores preferences under organization `RemarkSync`. On Windows the native
QSettings backend uses the current user's registry, rather than a repository
configuration file. Tests use isolated temporary INI files and must not alter
real preferences or start the user's automatic network sync.

| Application/key | Source default | Selected use on this PC |
| --- | --- | --- |
| `CloudSync/source` | `cloud` | `server` |
| `CloudSync/server_url` | Unset; `REMARKSYNC_SERVER` when it is set | This installation's private endpoint, entered once in the GUI |
| `CloudSync/server_destination` | Repository `output/server-sync` | The checkout's `output\server-sync` |
| `CloudSync/destination` | Repository `output/cloud-sync` | Separate direct-cloud store; retained when switching sources |
| `CloudSync/automatic` | `false` | `true`, while RemarkSync is open |
| `CloudSync/interval_minutes` | `5`, UI range 1–60 | `5` |
| `CloudSync/pdfs_only` | `false` | Whole library; this option applies only to direct cloud acquisition |
| `RemarkSync/library_path` | Unset | The checkout's `output\server-sync\raw` |
| `RemarkSync/handwriting` | `false` | Optional user choice; not required for preservation |
| `RemarkSync/pdf_column_notes_v2` | `false` | Optional user choice; default retains tablet placement |

Use **Library sync…** to switch sources and change polling. The two destination
preferences are separate, and the server store is bound to its source URL.
The GUI currently displays the destination but has no folder-picker for it;
use the CLI's `--dest` option for an explicit destination. `server-sync gui`
selects the server source; `cloud gui` explicitly selects direct cloud.
Neither GUI automation nor the CLI watch mode creates an OS service/login task.

No server address is built into the package. The GUI address field remembers
what you type, `REMARKSYNC_SERVER` supplies the CLI default, and
`server-sync --server HTTPS_ADDRESS` overrides both. Network server addresses require HTTPS; HTTP is allowed only
for localhost tests. Redirects are refused. Hostnames, ports and paths are
configuration, not secrets, but can reveal private infrastructure when shared.

## Installation addresses and deployment helpers

Nothing about a particular deployment is built into the package. These variables
hold what used to be hard-coded; the helpers refuse to run without them rather
than guessing.

| Variable | Purpose | Default |
| --- | --- | --- |
| `REMARKSYNC_SERVER` | Private server address for `server-sync` and the GUI field | Unset; `--server` or the GUI field otherwise |
| `REMARKSYNC_SSH_HOST` | `user@host` for `connect-server.ps1` and `pair-server.py` | Unset; both scripts refuse without it |
| `REMARKSYNC_SSH_KEY` | Dedicated deployment private key | Ignored `.tools/deploy-ssh/id_ed25519` |
| `REMARKSYNC_RELEASE` | Remote release directory holding `deploy/server/compose.yaml` | Unset; pairing refuses without it |

Addresses, hostnames and release paths are configuration rather than secrets,
but they describe private infrastructure: keep them in your environment instead
of in the repository. The GUI remembers a typed server address in QSettings, so
it is entered once per installation.

## Credentials and server environment

| Variable | Purpose | Current Compose value/default |
| --- | --- | --- |
| `REMARKSYNC_CLOUD_CLIENT` | Pinned rmapi executable | `/app/.tools/rmapi/rmapi` |
| `REMARKSYNC_CLOUD_PROFILE` | Protected account directory | `/state/cloud` |
| `REMARKSYNC_CLOUD_KEY_FILE` | Separate Linux encryption key | `/keys/cloud.key` |
| `REMARKSYNC_CLOUD_SESSION_DIR` | Linux plaintext working-session directory | `/run/remarksync`, service-owned RAM mount |
| `XDG_STATE_HOME` | Linux profile base when no explicit profile is given | `$HOME/.local/state` fallback |
| `LOCALAPPDATA` | Windows DPAPI profile base | `%LOCALAPPDATA%/RemarkSync/cloud` |

Windows stores `account.dpapi` under its protected profile with a restricted ACL.
Linux stores `account.fernet`; the separate Fernet key is required and is not
interchangeable with DPAPI. Linux private directories must be real directories,
owned by the service UID with private permissions. Existing profile/key material
must be retained together for account recovery; never copy it into the checkout.
Desktop server downloads require Tailscale access, not these account credentials.

The child rmapi environment clears inherited `RMAPI_*`/`REMARKSYNC_*` values,
then explicitly sets private token/cache/temp paths and bounded concurrency.
Do not override authentication hosts or force a schema version as an informal
fix. Pairing codes are entered through masked UI/interactive input and sent on
stdin; they are not arguments, saved preferences or documentation content.

## Storage layout

| Store | Use | Publication/history |
| --- | --- | --- |
| Existing SSH backup | Raw files from the tablet | `.remarksync-history`, journal and writer lock |
| `output/cloud-sync` | Direct acquisition/export on this PC | Raw projection, immutable raw revisions, versioned renderings and state |
| VPS `remarksync_library` volume | Central acquisition/export | Mounted `/data/library`; retained archives and renderings |
| `output/server-sync` | Verified desktop downloads | Separate server marker, raw projection and hash-addressed PDFs |
| VPS `remarksync_cloud-profile` / `remarksync_cloud-key` | Account and separate key | Profile writable by sync; key read-only; neither mounted into web |

Search and handwriting caches live alongside a raw library as private SQLite
files; they can be rebuilt. Raw archives, annotation scenes, originals and
retained exports are preservation data and must not be treated as disposable
caches. Do not replicate a live SQLite/WAL database by generic file sync.
Retention is currently indefinite. `output/server-sync` is the existing
off-server document/PDF backup, updated while the desktop app is open. It retains
revisions acquired by this client but does not fetch all pre-existing server
history or the server's account profile/key. The whole-library site ZIP additionally
covers retained server-only history and has a successful real offline restore.
Full replacement-service/account recovery and backup cadence remain pending.

The site also provides a whole-library ZIP containing retained server history;
`remarksync backup verify/restore` are offline operations. Backup limits and
download/recovery behavior are documented in [backups](backups.md). No new
QSettings or account credentials are required for this feature.

## Development and validation configuration

Production Python requirement is `>=3.11`; Docker currently uses
`python:3.13-slim-bookworm`. `pyproject.toml` keeps minimum-version ranges, not
a lock file. Linux test bootstrap pins `packaging>=23,<24` for the current
rmscene dependency constraint. A complete dependency/image digest policy is
still follow-up work; installed versions and image IDs are dated observations.

Ruff selects Pyflakes (`F`) rules. The Kiro save hook and documented lint command
include `src`, `tests`, `scripts` and `deploy/server`. Tests require `[dev]`;
the Linux profile needs `[server]`. OCR dependencies are optional and are not
installed/run by the server deployment.

Real-data tests use `REMARKSYNC_REAL_BACKUP` and `REMARKSYNC_DESKTOP_STORE`.
Device oracles additionally use `REMARKSYNC_TABLET` (default `10.11.99.1`),
`REMARKSYNC_DEVICE_PDF`, and the experiment-specific variables documented in
[tests/real/README.md](../tests/real/README.md). None should be committed with
private values. Real tests skip when their prerequisites are absent.
