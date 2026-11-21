# Data flow and repository hygiene

Updated 2026-10-03. This records implemented behavior and the practical
repository boundaries; it is not a complete security assessment.

## Where data goes

Viewing, search, handwriting recognition and compilation operate on local
copies. TrOCR may download model weights; it does not send the user's ink to
an external recognition API. Generated compiler archives are local until the
user imports them through the official app or tablet USB interface, or sends
them with **Send Markdown to Tablet** / **Edit Document**. Those upload one
chosen archive to reMarkable Cloud from the desktop, after a confirmation, and
keep what was sent (and any version it replaced) under ignored `output/sent`.

The tablet's account sync sends documents to reMarkable Cloud. The selected
RemarkSync workflow downloads those documents to the user's own VPS,
then serves verified archives/PDFs to tailnet devices and the desktop PC over
private HTTPS. Direct desktop cloud acquisition is an optional separate path.
The acquisition/download services do not upload, edit or delete cloud documents.
Registration/token renewal does perform authentication requests.
Manually invoked writing experiments in `tests/real` can create tablet documents;
they are not part of automatic sync or the ordinary synthetic test run.

Server raw documents and exported PDFs are ordinary files in the library volume;
Fernet protects the account profile, not the library itself. Tailscale/HTTPS
protect transport and restrict access to the existing private endpoint. The
web application has no additional per-document authentication; tailnet access
policy is the authorization boundary. No public Funnel was enabled. This setup
does not establish device-wide disk encryption. Verified desktop downloads are
an independent off-server document/PDF backup: earlier acquired revisions remain
locally, and server disappearance does not delete them. Freshness depends on the
last successful desktop sync; older server-only history and server credentials
are outside that desktop download. The site's whole-library ZIP additionally
includes retained server history, verified archives/renderings and allowlisted
restoration metadata. Its read-only worker excludes credentials, mutable raw
projection, caches and temporary files. Offline restore refuses existing stores.
See [backup scope and limits](backups.md) and current status for restore evidence.

## Credential boundaries

- Dedicated SSH key: ignored `.tools/deploy-ssh/id_ed25519`; not a general
  browser/server credential. The public key was installed with `restrict` on
  `$REMARKSYNC_SSH_HOST`; do not print private key bytes.
- Windows account: private ACL plus DPAPI under `%LOCALAPPDATA%/RemarkSync/cloud`.
- Linux account: UID 10001, private profile, encrypted `account.fernet`, separate
  mode-0600 `/keys/cloud.key`; transient plaintext sessions are in RAM.
- Web container: read-only library only, no account profile/key mounts.
- Desktop download: no reMarkable account credentials required.
- Pairing: masked field/stdin; neither code nor tokens belong in argv, logs or Git.

Container root filesystems are read-only, capabilities dropped, privilege
escalation disabled, resources/logs bounded, and persistent mounts use `nocopy`.
The initializer retains an existing key. Existing Docker sudo access was used;
no SSH group/sudo policy was broadened for this deployment.

## Files excluded from Git and container builds

`.gitignore` excludes local dependencies/tools, `output`, `tmp`, `demo`, personal
document formats/raw metadata, archives, environment/private settings, keys,
encrypted profiles, token caches, runtime state, histories, logs and SQLite files.
Safe `.env.example/.sample/.template` names are allowed, but must contain only
placeholders. No environment template or secret-valued configuration is required
by the present deployment.

`.dockerignore` starts from a source allowlist and then denies secret/document
patterns even under `src`. `scripts/package-server.py` uses an explicit source
file list intersected with versionable Git paths plus a hygiene preflight; it
never packages ignored scratch, `.tools`, local documents,
credential volumes or local settings backups. The Linux client is downloaded
and checksum-verified during the build, rather than copying a local binary/key.

Ignore rules do not remove already tracked files and can be bypassed with
`git add -f`. Review `git diff --cached --name-only` and the staged diff before
committing. If a real secret is ever committed, protect the account first;
deleting the working file alone does not remove Git history.

## Repeatable repository checks

```powershell
.venv/Scripts/python scripts/check-repository-hygiene.py --history
.venv/Scripts/python -m ruff check src tests scripts deploy/server
git diff --check
git status --short
git diff --cached --name-only
```

The read-only hygiene script checks ignore probes, versionable working files,
index blobs and optionally all reachable history blobs. It detects private-key
headers, common GitHub/AWS token formats, JWTs and long cloud-token scalars,
printing paths/categories rather than matching values. It does not read ignored
private profiles or keys, inspect remote repositories/dangling objects, or claim
to detect every possible credential format. False positives require review.

The final 2026-10-03 housekeeping scan checked **107 versionable files, 325
reachable/index Git blobs, 46 ignore/source probes and 64 local Markdown links**
with no findings. Existing runtime directories remain ignored, and no tracked
secret matching the checked patterns was found. Source packaging was exercised
locally; tests also verify ignored source is omitted and symlinks are refused.
Docker's exclusion ordering was reviewed, but this documentation pass did not
rebuild an image. No history rewrite or key rotation was performed because this
check did not establish a credential exposure.

## Pending boundaries

Desktop sending uses the desktop's own cloud pairing. [Server uploads](spec/server-uploads.md) are proposed. They must use a dedicated
validated submission path and worker, retain the web container's credential
isolation, and require an explicit chosen document/destination. Preservation
PDFs must never become automatic replacements for editable tablet archives.
Complete replacement-service/account recovery, backup freshness policy, retention
policy and a tested dependency snapshot
remain pending in the [roadmap](research/review-fixes-and-feature-roadmap.md).
