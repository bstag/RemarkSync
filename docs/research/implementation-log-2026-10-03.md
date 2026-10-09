# Implementation and validation log — 2026-10-03

Historical sequence retained from the earlier roadmap. Test counts and
future-tense recommendations in earlier sections describe those stages.
[Current status](../current-status.md) and the
[remaining roadmap](review-fixes-and-feature-roadmap.md) supersede them for
today's behavior and priorities. No live cloud upload was implemented in this log.

Updated 2026-10-03. The original assessment is in [system-review-2026-10-03.md](system-review-2026-10-03.md); external API/protocol evidence is in [cloud-sync-and-pdf-preservation-sources.md](cloud-sync-and-pdf-preservation-sources.md).

## Fix status

| Finding | Implemented change | Verification |
| --- | --- | --- |
| F1: replacement can lose saved documents | Validate document metadata/content, base documents and a complete source entry manifest; check the source signatures again before publication. Retain the old raw revision, journal publication, roll back failed moves and recover interrupted publications on the next pull. OS writer locking and per-batch staging prevent overlapping pulls. Pruning retains the old raw revision too. | Missing batch/payload, partial move, interrupted publication, retained/pruned history, competing writer and read-only dry-run tests |
| F2: unsafe extraction | Reject tar links and special types before writing; extract only regular files/directories; check paths and extraction limits independently of Python's defaults. ZIP extraction also checks size/count, links and destination emptiness. | Hard-link escape, traversal, oversized archive and existing-destination preservation tests |
| F3: original PDF page mismatch | PageRef retains page ID, source-PDF index, page metadata and logical position. Modern pages use redir/deletion/order fields; legacy pages use redirectionPageMap. Inserted pages do not inherit another page's ink or base PDF. Both the viewer and decoding layout use the resolved source page. | Reordered, inserted, deleted and legacy mapping tests plus a GUI render-source check |
| F4: unsupported PDF threading | Keep PDF access on the GUI main thread. The decoding worker receives plain page sizes; it does not open PDF handles or use MuPDF. | Worker test rejects any attempt to open a PDF; normal asynchronous GUI/PDF smoke tests |
| F5: GUI compiler failure | Use the existing _library property, inside the error-handled compile operation. | Real GUI action tested with and without a library |
| F6: unreadable pages vanish silently | Decode read/parse errors become visible page messages and tooltips. Failed indexing is reported and rolls back to the last good document text, including during rebuild. Failed documents retry; scene/mapping/conversion hashes notice repairs without metadata changes. CLI reports failures and exits nonzero. | Corrupt/repair, metadata-preserving edit, rebuild rollback and GUI error-display tests |
| F7: highlight metadata lost | Preserve snapped-highlight color/opacity, inherited visibility, layer identity and anchor translation. Preserve text visibility too; hide invisible content consistently in painting, extents, search and handwriting recognition. | Colored/hidden/anchored scene tests and existing decoder/recognizer tests |
| F8: Paper Pro underlay center mismatch | Center the original PDF using that page's declared width, keeping the measured PDF scale. | Annotation-to-PDF-center regression and existing geometry tests |
| F9: later Paper Pro templates absent | Compare canvas height with the current page's paper height. | Normal later Paper Pro template test and existing endless-page tests |

Also corrected the unused import, invalidated cached target-paper size on rescan, bounded PDF raster caching with size-aware keys, streamed SSH output to temporary disk rather than retaining the full tar in RAM, and kept thread references when cancellation times out rather than treating the worker as stopped. GUI operations report failures opening/recovering a library.

## Storage behavior

The current raw folder layout remains compatible. A replacement is recoverable, but publishing multiple flat files is not a single atomic snapshot for arbitrary external readers. Opening/indexing a library detects outstanding publication journals; existing readers may still overlap a subsequent pull. An immutable snapshot store with a single published manifest remains part of the hosted-worker milestone. This distinction matters before automatically processing and synchronizing the same folder concurrently.

Previous raw revisions live in `.remarksync-history/<uuid>/<revision>/`, with a `.revision.json` manifest. They are retained without automatic expiration. Storage usage will grow; a future retention policy should distinguish deliberate preservation from cache cleanup. Recovery is exercised for interrupted processes/file-operation failures; it is not a guarantee against failed hardware, disk corruption or every power-loss scenario.

## Verification and remaining fidelity limits

The synthetic suite finished with **158 passed and 11 skipped**, and lint is clean. Read-only real-data validation used the existing raw backup and desktop cache: six checks passed, one v5 ink-mass check failed, and three device-export checks skipped because cached tablet PDF exports are absent.

The v5 failure was reproduced against untouched committed source in an isolated temporary checkout: median ink-mass ratio **1.177** in both versions, against an expected maximum of **1.15**. This is an existing renderer/oracle calibration discrepancy. No threshold was loosened and no speculative pen correction was added. Current fixes do not establish full preservation fidelity for those older pages.

The real backup contains 24 PDF/EPUB base documents and 2,018 modern pages: every one of those pages has both redir and idx fields. This supports the schema choice against actual local data; separate real reordered/inserted/cropped/rotated export fixtures are still required before claiming export fidelity. Existing above-paper/below-paper framing, brush texture, templates and substitute-font limitations remain documented in README/ADRs.

## Walkthrough of the end goal

### 1. Preserve an annotated copy of the original PDF locally

User flow: select a document → Export annotated PDF → choose a destination. The exporter starts with a separate copy of the original PDF and adds highlights, handwriting, drawings and typed notes. Original text stays searchable. Inserted pages and notes beyond the original page edges become supplementary pages, so preservation does not quietly clip them.

Retain the original PDF bytes unchanged, the annotated derivative, and the raw tablet revision. Offer a flattened visual edition later, ensuring all note text is visible before annotation metadata is flattened. Add a manifest containing original checksum, source UUID/revision and export warnings. Export must fail/report omitted unreadable content, rather than declaring an incomplete artifact complete.

Implementation: a Qt-free export function shared by CLI and GUI, using resolved source-page mappings and tested tablet-to-PDF transforms. Use vector ink outlines or transparent overlays for variable-width pens, native PDF highlights where suitable, and text for typed notes. Cover rotation, crops, duplicates, inserted pages, hidden layers, colored highlights and margin notes. Validate against fresh tablet/official exports, including the outstanding v5 discrepancy, before relying on the output for preservation.

### 2. Remove the direct tablet-to-PC connection

User flow: pair a read-only cloud source once → Sync now or automatic polling → new tablet edits arrive in RemarkSync → affected preservation PDFs are regenerated. The tablet syncs over Wi-Fi to reMarkable Cloud; the PC downloads from that account. The PC needs internet access and must be running for local processing, but does not need a cable or a direct network connection to the tablet.

Implementation: prove a pinned community rmapi client's raw archive downloads and token renewal using one representative annotated PDF, then add a source adapter. Keep credentials in protected storage, compare revisions by UUID/hash, preserve historical data and report failures/freshness. Account disappearance or an incomplete listing must not delete preserved revisions. Confirm the account's Connect/sync coverage during the compatibility trial. Use the community client only for transport; preserve/render annotations in the tested RemarkSync exporter.

### 3. Continue syncing and preserving while the PC is off

User flow: the tablet reaches Wi-Fi → a hosted worker downloads and validates changes → new raw revisions and annotated PDFs are saved → open/download them when convenient. The desktop app becomes one client of the preserved library.

Implementation: run proven acquisition/export jobs on an always-on Python container/VM, isolate concurrent PDF jobs by process, publish immutable snapshots and artifacts to versioned durable storage, retain protected authentication state, and recover jobs after restart. Store rebuildable indexes/caches separately from authoritative raw revisions. Add authenticated artifact retrieval and optional browser viewing only after the worker is dependable.

### Current first feature

Local original-PDF preservation is now implemented in `remarksync export` and the GUI's **Export Annotated PDF…** action. It retains original page order, original text/comments/attachments, embeds the untouched original and raw active scene bytes, paints visible highlights/ink/typed notes, and appends inserted/duplicate/overflow sheets. Export refuses unsafe destinations, unreadable scenes, invalid mappings, incomplete publication journals and changed input. The GUI isolates MuPDF in a separate process.

Synthetic regression checks cover crops and all four rotations, hidden layers, Unicode, source changes, existing PDF annotations/attachment collisions, CLI errors and actual GUI subprocess completion. A real 53-page document was exported locally and inspected on three annotated pages; its manifest contains 338 highlight groups and its PDF embeds 45 raw scenes. This verifies actual backup acquisition and preservation plumbing, not exact device fidelity.

After this feature, the complete default suite has **186 passed and 12 skipped**, and lint passes. The enabled real suite has **7 passed, 1 existing v5 parity failure and 3 missing-device-export skips**. The final real preservation test also passed separately after the last exporter changes. The v5 median remains exactly 1.177; the exporter records the limitation rather than adjusting the comparison threshold.

The fidelity gate remains open: resolve the existing v5 discrepancy and compare fresh tablet exports for device zoom/pan, Paper Pro and other representative page transforms. Font substitution, brush texture and undrawn inserted-page templates are explicit warnings/limitations. Read-only cloud acquisition is implemented and the live single-document Windows trial succeeded (details below). Wider account/device coverage remains to be verified. Hosting follows after reliable ongoing acquisition.

### PDF body-column note layout

The GUI offers optional **Match PDF Text Width**, and CLI exports accept `--text-layout pdf-column`. After clarification that the reported offset affected highlights in Codex's preview, the default was restored to tablet text placement and line breaks. The preview issue did not reproduce in the user's other PDF reader. With reflow enabled, visible typed notes align to the original PDF's inferred body-text column and wrap with the actual export font's metrics. Column inference weights font sizes by body character count, groups nearby left edges and keeps separate columns separate. A note uses the column closest to its original horizontal position; inserted sheets borrow the nearest base page's measure. Image-only/unsupported horizontal layouts fall back to page margins with a warning.

The default is `--text-layout tablet`; reflow is opt-in. Reflow retains the first vertical position and paragraph styles, and grows supplemental context sheets when needed. Highlights and handwriting keep source positions; handwritten marks anchored to old typed lines may therefore no longer line up with the reflowed text. The manifest stores the chosen mode/columns and the unchanged original text-block model. Raw scenes stay untouched.

### Read-only cloud trial implementation

The Windows x64 client is pinned to rmapi v0.0.35 and its release ZIP SHA256. The installer places it in ignored `.tools/rmapi`; its source/protocol/credential audit is in [cloud-trial-client-audit.md](cloud-trial-client-audit.md). The client is used only for pairing and fresh listings. A separate Python adapter downloads known blob hashes with GET, verifies every size/SHA256, validates archive paths and document identity, and preserves exact raw metadata/content instead of reconstructing rmapi's lossy cache projections. Tokens use private Windows ACLs and per-user DPAPI encryption; endpoint overrides/tracing are cleared, and transient plaintext token/cache files are removed after each operation.

`remarksync cloud` and **Cloud trial…** open a masked pairing/list/download dialog. Processing runs in a child process. A selected PDF/EPUB becomes an immutable UUID/hash directory containing raw `.rmdoc`, preservation PDF, and file/output checksum report. The source revision is reread before publication; prior revisions survive failures and cloud disappearance. Unchanged archives are checksum-checked and reused. The trial never writes cloud documents and never updates the existing SSH backup.

Before live pairing, the default suite had **213 passed, 12 skipped**, lint clean. Eighteen cloud tests cover exact raw preservation, checksum/size validation, source changes, failed/corrupted archives, unsafe filenames, credential redirects, subprocess whitelist/stdin pairing, isolated cache/endpoint environment, Windows ACLs and DPAPI. GUI smoke tests also check masked input, worker failure/close behavior and the tablet-layout default. The subsequent pairing-button regression also passes. Automatic polling, hosted execution and wider account/device coverage are later gates.

### Live cloud trial result (2026-10-03)

Account pairing and subsequent authenticated listings succeeded. The initial pairing URL was corrected after its 404 was reproduced; `/device/browser/connect` reaches the live sign-in flow and is now used by the app/CLI. A button regression test passes (19 relevant cloud/button tests passed after that fix; no full-suite rerun was needed for the one-line URL correction).

`Discovery Days (updated 2-2023).pdf` (UUID `b4ec01c3-61f9-4758-89b5-7ed041e732db`) was downloaded directly from cloud revision `4ffa5efdb5b547f30782c3044217c47ca1cce48a62b94710b6c25a02cc0f7f5f`. All **49 files** are byte-identical to the existing USB backup, including the base PDF, raw metadata/content and **45 scenes**. No cloud document writes occurred. The separate preservation PDF has **53 pages** and **338 highlight groups**, embeds the exact base PDF and all 45 active scene files, and retains the original searchable words and their boxes across every original page. Poppler rendering of pages 2-4 was visually inspected; highlights (including “leadership”) and handwriting render correctly.

Artifacts and machine-readable verification are stored in ignored `output/cloud-trial/<UUID>/<revision>/`; `scripts/verify-cloud-trial.py` reproduces the read-only USB comparison and PDF attachment/text checks. A second authenticated trial reused the unchanged, checksum-verified revision; no transient credential sessions remained. This establishes one real annotated PDF's cloud acquisition and preservation parity with USB. Actual expired-token renewal, automatic polling, account-wide coverage, hosted processing and fresh device-export fidelity comparisons remain unverified/pending. The fixed-scale zoom/pan caveat is retained in the PDF manifest.

### Automatic whole-library cloud sync (2026-10-03)

Whole-library sync is implemented in `cloudsync.py` and enabled in the user's cloud window. `remarksync cloud sync` performs one pass; `cloud watch --interval 300` polls in a foreground process. The GUI's **Cloud sync…** window remembers automatic polling, defaults to five-minute checks, resumes on main-app startup, reports per-document progress/freshness/errors, backs off after failures and prevents overlapping jobs. **Open downloaded library** reuses the current controller, and **Open preservation PDFs** opens a named export index rather than requiring UUID navigation. Network and PDF work remain in a separate process.

Cloud folder metadata is now acquired alongside documents. A separate owned cloud store contains `raw/` for the current library, immutable `revisions/<UUID>/<hash>/document.rmdoc` archives, preservation PDFs and checksum reports. Raw publication reuses the reviewed writer lock/history/journal/recovery mechanism. A final fresh listing checks source revisions before publication. Missing cloud documents do not delete local copies. Raw acquisition survives renderer failures, and missing/failed exports retry from verified raw archives. A failed authorization GET triggers one bounded token-refresh/listing retry. Existing archives/PDFs are verified before reuse; damaged current raw files can be repaired from the verified archive. Unknown/nonempty destinations are refused to avoid writing into the USB backup.

The first live whole-library pass acquired **43 documents and 10 folders**, verified **373 raw files**, and generated **11 preservation PDFs** (10 PDFs plus the EPUB's cloud-provided PDF rendering). The second pass reported **53 unchanged, zero downloads, zero raw/export failures**. The GUI's automatic startup pass also completed successfully, persisted the enabled five-minute setting and reported 11 exports available. Of the 373 raw files, 369 matched the USB backup; the four differences are the 2026 notebook's content/metadata/one scene and one README content file, all preserved from the hash-verified cloud revision. The original USB backup was untouched.

Every preservation export was reopened and its original PDF attachment and active raw scene attachments verified against its cloud archive. Representative pages were rendered with Poppler; the Life Group manual's original page clips an outlying note by design, while appended context page 57 preserves that note in full. New-format decoder diagnostics occur on 78 Bible pages, 4 handwriting-workbook pages and 1 reManager guide page; their complete raw files are preserved, and derivative warnings remain explicit. This is acquisition/preservation verification, not proof of complete renderer fidelity for unsupported scene fields.

The full default suite has **229 passed, 12 skipped**, with lint clean. Additional tests cover folder/notebook hierarchy, incremental reuse, history, cloud disappearance/source changes, corrupt authoritative archives, repairing current files, PDF failure/retry, bounded expired-token retry, outage status/last-success preservation, GUI timer overlap/stop/backoff/progress, and sharing one polling controller with the open library. Real expired-token renewal and sustained polling through future tablet edits still need observation; hosted processing while the PC is off is the next deployment milestone.

Column-layout verification finished with **193 passed and 12 skipped** in the complete default suite, clean lint, and a visually inspected example using actual serialized tablet note text. New checks measure exported glyph positions against detected one/two-column margins, cover long words/lists/Unicode/overflow and image-only fallback, preserve hidden/raw notes, and exercise column layout through the GUI subprocess.

### Hosted server acquisition and private browser library

The paired Ubuntu 24.04.4/x86-64 Docker deployment now runs independently of
the desktop PC. `remarksync-sync-1` checks the whole reMarkable Cloud library
every five minutes; `remarksync-web-1` serves only the read-only library through
private Tailscale HTTPS at `https://server.tailnet.ts.net:8443/`. The web
container has no account profile or encryption key. Linux account tokens use
authenticated encryption with a separately mounted private key, and temporary
plaintext sessions remain in RAM. Both services run as UID/GID 10001, have
`unless-stopped` restart policies, and opt out of the VPS's existing Watchtower.
Existing applications and network listeners were retained.

The first hosted pass preserved **44 documents, 10 folders, 54 immutable raw
archives and 12 PDFs**, with no acquisition/export failures or cloud document
writes. Verification checked **377 cloud-indexed source files, all 12 original
PDF attachments and 136 scene attachments**. A service restart retained the
encrypted account and reused **54 unchanged revisions**, with no downloads or
failures. Real HTTPS PDF HEAD/range and notebook archive requests passed, and
the private browser page was inspected. Poppler inspection of the Life Group
manual's annotated page 2 and appended page 57 confirmed the overflow note is
fully preserved on the supplementary sheet. Storage was about 174 MiB and each
idle service used about 41 MiB RAM.

Two deployment bugs were reproduced and corrected: the pairing window's
completion handler collided with QDialog's built-in `finished` signal, hiding
SSH completion; four focused tests now cover stdin-only transport and actual
success/error signal delivery. Docker also reset ownership of initialized
empty credential/library volumes during automatic image-file copying. All
mounts now use `nocopy`; a disposable-volume comparison reproduced the reset
and verified the fix. Repeated real-container runtime checks pass after volume
initialization and web-service recreation, with the existing key unchanged.
See [the deployment guide](../../deploy/server/README.md), runtime check and
read-only library verifier for repeatable validation.

The newer-scene decoder, typed-font substitution and fixed-scale placement
warnings remain explicit; raw annotations are retained without claiming full
renderer fidelity. A fresh Discovery Seminar page-2 tablet edit was subsequently
observed through the unforced poll at 22:11 UTC: 1 changed, 53 unchanged and no
failures, with five new highlight groups and the original/previous revision
retained. Full verification now covers 378 source files and 137 scene
attachments. The user confirmed yellow on the tablet. The decoder now treats
only `YELLOW` paired with opaque-black RGBA `(0, 0, 0, 255)` as a yellow
placeholder; custom colors and partially transparent black are unaffected.
Regression tests reproduce that encoding and verify yellow PDF pixels.
The latest source revision was regenerated offline into a separate
hash-addressed rendering while retaining the earlier gray PDF. Its private
HTTPS download matches the server checksum, and Poppler inspection confirms
all five highlights are yellow and aligned. The next sync reused all 54
revisions with no failures and retained the corrected PDF. The Windows suite
passes with 267 tests and 16 skips; the deployed Linux image passes 110 tests
with two Windows-only skips. Its page 2 still has a newer-format reader warning,
with raw bytes retained. Off-server backup,
notebook PDF generation and broader account/device coverage remain follow-ups.

### Central server to desktop downloads

`serversync.py` implements private HTTPS manifest/archive/PDF acquisition without
cloud pairing or local PDF rendering. The explicit manifest allowlist excludes
account credentials, local paths and internal reports. Redirects are refused;
download hashes, archive membership and extracted source hashes are verified
before publication. The separate `output/server-sync` store is bound to its
source URL. Raw history and earlier PDF renderings are retained; server absence
never propagates local deletion. Same-raw-revision PDF corrections download
only the new PDF. Network loss preserves offline access and triggers backoff.

The **Library sync…** dialog now selects direct cloud or the central server,
with separate destinations. Server polling shares the existing child-process
controller, overlap prevention and startup scheduling. `server-sync sync/watch/gui`
provides terminal access. This PC's default is its own server, automatic five-minute
checks while RemarkSync is open, and the downloaded raw library.

The first real HTTPS download preserved 44 documents, 10 folders, 54 archives
and 12 PDFs. All 378 source files, 12 original attachments and 137 scene
attachments passed verification. The next CLI and actual GUI child-process
checks reused all 54 revisions without downloads. The deployed web image passes
129 Linux tests with two Windows-only skips; its update left acquisition running.
Synthetic HTTP tests cover changed source revisions/renderings, local history,
notebook/folder downloads, offline/absent sources, corrupt artifacts, redirects,
source separation and symlinks. GUI tests isolate settings to avoid touching
the user's real automatic sync configuration.
