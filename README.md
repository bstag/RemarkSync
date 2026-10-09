# RemarkSync

A reMarkable backup viewer, local search index, Markdown compiler, cloud sync
service and original-PDF preservation tool. Local viewing works offline;
optional cloud acquisition can run on an always-on server.

The selected installation runs **a private server → desktop downloads**.
See [current status](docs/current-status.md) for what is deployed and verified,
[the roadmap](docs/research/review-fixes-and-feature-roadmap.md) for next work,
and [the documentation index](docs/README.md) for settings, scripts and evidence.

```
Tablet --Wi-Fi--> reMarkable Cloud --> server acquisition/preservation
                                              |
                                      private Tailscale HTTPS
                                      /                   \
                               browser library        desktop downloads
                                                            |
Tablet --SSH/USB--> separate raw backup -------------> RemarkSync app
                                                      viewer / local search
                                                      preservation PDFs
                                                      Markdown -> .rmdoc
```

## Install & run

```bash
cd E:\Source\RemarkSync
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m remarksync        # GUI
```

CLI:

```bash
.venv\Scripts\python -m remarksync pull C:\Backups\reMarkable\raw   # update backup from tablet (USB), changed documents only
.venv\Scripts\python -m remarksync scan  C:\Backups\reMarkable\raw  # print document tree
.venv\Scripts\python -m remarksync index C:\Backups\reMarkable\raw --handwriting  # search index, reading handwriting too
.venv\Scripts\python -m remarksync compile notes.md notes.rmdoc     # Markdown → .rmdoc
.venv\Scripts\python -m remarksync export C:\Backups\reMarkable\raw C:\Preserved\paper-annotated.pdf --document DOCUMENT_UUID
.venv\Scripts\python -m remarksync export paper.rmdoc paper-annotated.pdf
```

Flags worth knowing: `pull --dry-run` reports what would be fetched without
writing anything, `--full` re-copies every document and `--prune` deletes
local documents the tablet no longer has; `index --rebuild` re-indexes from
scratch and `--engine` picks the handwriting engine; `compile --library` or
`--paper WxH` compiles for a particular tablet's page size and `--title` names
the notebook the tablet shows (it defaults to the Markdown file's name).

Changed and pruned documents retain their previous raw files under
`<backup>/.remarksync-history/<document-uuid>/<revision>/`. Pulls validate
metadata, content and the source file manifest before replacing a document.
A writer lock prevents overlapping pulls; a recovery journal restores an
interrupted replacement on the next normal pull. A dry run writes nothing
and asks for recovery first if an interrupted pull remains. Keep the history
with the backup; pruning the visible library does not erase history or reclaim
that space. The flat backup format remains compatible with existing libraries,
but it is not a snapshot-isolated store for simultaneous external readers.

In the window, F5 rescans the open library and Ctrl+plus / Ctrl+minus zoom
the page view.

## Cloud acquisition and central-server downloads

The tablet can sync to reMarkable Cloud over Wi-Fi; this PC downloads from
your account without a cable/direct tablet connection. The PC must still be
running for direct local download/export. A private server can run acquisition
and preservation independently of this PC; its desktop-download workflow is
described below. The Windows installer is needed only for direct cloud pairing,
not for downloading from the central server.

```powershell
./scripts/setup-cloud-client.ps1
.venv/Scripts/python -m remarksync cloud
```

Choose **Library sync…** in the GUI and select **reMarkable Cloud directly**,
or use the standalone cloud window above.
Open [reMarkable's browser pairing page](https://my.remarkable.com/device/browser/connect), generate an eight-character device code,
and enter it in the masked field. **Sync now** acquires the whole library. Tokens
are protected with Windows DPAPI under `%LOCALAPPDATA%/RemarkSync/cloud`,
with a private Windows ACL; plaintext tokens/cache exist only during the
operation in that private profile. Do not share tokens or pairing codes.

Click **Sync now** to download the whole library, including notebooks and
folders. **Open downloaded library** opens it in RemarkSync, and **Open
preservation PDFs** shows a named list of the current exports and their
warnings. Enable **Sync
automatically while RemarkSync is open** for five-minute checks (adjustable
from 1-60 minutes). This preference resumes when the main app starts again.
Only changed cloud revisions are downloaded. Earlier raw files and PDF
exports are retained; cloud disappearance is reported without deleting local
copies. Failed checks back off, and overlapping operations are refused.
Stopping automatic checks lets a running acquisition finish safely.

Each PDF/EPUB with an available base PDF gets a preservation copy. Raw files
are retained even if export fails; those exports remain retryable. Decoder
warnings are recorded in the PDF manifest and revision report. An EPUB with
no cloud-provided base PDF needs a separate conversion/rendering step before
preservation export is possible. Notebook pages remain available in the raw
library/viewer; notebook-to-PDF export is a separate feature.

The installer pins the separately distributed AGPL `ddvk/rmapi` v0.0.35
Windows client and verifies the release ZIP's SHA256. It uses rmapi only for
pairing/listing. RemarkSync downloads every indexed document file by hash,
checks sizes/SHA256, preserves exact raw content/metadata, exports notes,
and checks that the cloud revision is still current before publication.
Cloud documents are never uploaded, changed or deleted by this sync.
Pairing/renewal registers account credentials through authentication POSTs.
This uses a community client/protocol. A live trial matched all 49 files of
one annotated PDF against the USB backup, preserving 338 highlights and 45
raw scenes. Whole-library acquisition and server downloads have since been
verified for this account; expired/revoked credential recovery and broader
device/account coverage remain follow-ups. [Pinned client audit](docs/research/cloud-trial-client-audit.md).

The default sync store is `output/cloud-sync/`: `raw/` is the downloadable
library; `revisions/<UUID>/<cloud-hash>/` holds `document.rmdoc`, optional
`annotated.pdf`, and `revision.json` with raw file/output checksums and export
warnings. `sync-state.json` records progress, failures and last full success.
Corrected renderings can live under `renderings/<PDF-sha256>/annotated.pdf`
within the raw revision, selected by the current record without replacing
earlier PDF bytes.
`preservation.html` is the clickable export list.
Interrupted raw publication is recovered before the next sync. A destination
must be empty or an existing cloud store, so the SSH backup remains separate.

For terminal use: `remarksync cloud pair` (masked prompt), `cloud list`,
`cloud sync --dest output/cloud-sync`, or `cloud watch --interval 300` for a
foreground polling process. Add `--pdfs-only` to limit acquisition to PDFs,
EPUBs and folders. Account login/pairing is a human step. The earlier
single-document trial remains available as `cloud trial --document UUID
--dest output/cloud-trial`; its archives/verification remain separate.

For an Ubuntu VPS, [the Docker/Tailscale deployment package](deploy/server/README.md)
adds Linux encrypted credential storage, separate persistent volumes and a
private browser library. The deployed server is paired separately and runs
whole-library sync every five minutes independently of the PC. Its first pass preserved 44 documents,
10 folders and 12 PDFs; the restart check reused all 54 revisions with no
downloads or failures. Read-only verification checked all 377 raw files and
the PDFs' 12 original attachments and 136 scene attachments. The private library
is at `https://server.tailnet.ts.net:8443/` for devices on the tailnet.
A fresh page-2 highlight on an annotated PDF was acquired through the normal
polling cycle with 1 changed document and no failures; the previous revision
and untouched original remain preserved. After the user confirmed yellow,
the conflicting yellow/opaque-black encoding was corrected and the latest PDF
was regenerated with yellow highlights. The earlier gray rendering remains
stored, and the next automatic sync retained the corrected PDF.
Latest whole-library verification includes 378 raw files and 137 scene
attachments; the preceding 377/136 counts describe the initial hosted pass.
The desktop's verified downloads provide an off-server backup of acquired
documents and preservation PDFs. The site's whole-library ZIP also covers
retained server-only history, with offline verification/restoration proved on
the real library. Full replacement-service/account recovery remains follow-up.
The Windows GUI and DPAPI
profile continue to work as before.

### Download from the central server to this PC

For a one-file backup including all retained server history, use **Download
whole-library backup** on the private site. Its dated ZIP includes archives,
preservation PDFs, checksums and offline restoration instructions. Account
credentials remain separate. See [backup and restore](docs/backups.md).

In **Library sync…**, select **Central preservation server** and enter your
server's HTTPS address, such as `https://server.tailnet.ts.net:8443`. The field
remembers it, and `REMARKSYNC_SERVER` supplies it to the CLI; connect Tailscale
first. No address ships with the package.
**Sync now** downloads the whole library, including folders and notebooks,
and the server's already-preserved PDFs. No desktop pairing or PDF rendering
is required. **Open downloaded library** opens the raw files for viewing/search;
**Open preservation PDFs** opens the local export list. Copies work offline.

**Sync automatically while RemarkSync is open** checks every five minutes,
resumes on app startup, and backs off when the server is unavailable.
This installation is configured to use its own server with automatic checks
enabled and the downloaded library selected. The first download verified 44 documents,
10 folders, 54 archives and 12 PDFs; a second pass reused all 54 entries.

The separate desktop store is `output/server-sync/`. Every archive, extracted
source and preservation PDF is checked against the read-only server manifest.
Changed raw revisions and PDF renderings are retained separately; corrections
to a PDF are downloaded even when its raw revision is unchanged. Documents
absent from the server remain locally available. Downloads include currently
published revisions; earlier history already on the server stays there.
The existing USB backup and direct-cloud store remain separate.

Cloud sending from RemarkSync is the next proposed feature. The current
compiler saves locally; there is no **Compile and send** button yet. See the
[upload specification](docs/spec/server-uploads.md).

Terminal commands: `remarksync server-sync sync`, `server-sync watch --interval 300`,
or `server-sync gui --auto`. Use `--server HTTPS_ADDRESS` and `--dest DIRECTORY`
for another private server and a separate local store. The watch command runs
while its process is active; automatic GUI checks run while the app is open.

## Preserve highlights and notes in PDF

Open a PDF/EPUB document, then choose **Export Annotated PDF…** (`Ctrl+E`).
Tablet text positions and line breaks are retained by default.
Optional **Match PDF Text Width** makes typed notes align and wrap
to the original PDF's detected body-text column, using the export font's
widths. Separate columns stay separate; a note uses the column closest to its
original horizontal position. Scanned/image-only PDFs use page margins when
there is no usable text column. Turn the option off to retain tablet text
placement and line breaks. Handwriting and highlights always keep their
positions, so reflowed typed notes may no longer align with handwriting that
was anchored to the original text lines. Raw scenes and original note positions
remain preserved in the attachments/manifest.

Save outside the raw backup. The GUI runs PDF export in a separate process,
so the viewer stays responsive. Wait for export completion before closing
the window. The CLI accepts a backup folder plus `--document UUID`, or a
single `.rmdoc` archive. Use `scan` to list documents; the UUID is the raw
document filename stem. Existing exports require `--overwrite` in the CLI;
the GUI uses the save dialog's replacement confirmation.
CLI exports default to `--text-layout tablet`; use `--text-layout pdf-column`
to enable reflow. Inserted note sheets in reflow mode borrow the nearest base
page's column width, adjusted to fit their paper.

The result retains the original PDF's page order, page boxes, rotation,
searchable text and existing comments. Visible snapped highlights become
PDF highlight annotations; handwriting is vector ink and typed notes stay
searchable. Original pages deleted on the tablet remain in this preservation
copy. Inserted note pages, duplicate source pages and full context sheets for
notes outside the original page edges are appended. The manifest maps each
tablet page to its output page(s), including when the tablet reordered pages.

PDF attachments contain the untouched original PDF, the original `.rm`
scene bytes (including hidden layers), and `remarksync-manifest.json` with
checksums, page mappings, note text, decoder diagnostics and fidelity warnings.
Existing attachment names are preserved; new attachments receive a numbered
suffix on collision. Keep the raw backup/history as well: these attachments
are preservation evidence, not a complete replacement for the raw store.
Files are published only after decoding, reopening the export and rechecking
captured source bytes. Failed decoding or changed input leaves an earlier
export untouched.

This first exporter uses the measured fixed tablet-to-PDF scale. Synthetic
tests cover original PDF crops/rotations, but device pan/zoom settings still
need comparison with fresh tablet exports. Typed notes use substitute fonts,
pencil/paintbrush texture is approximate, and older v5 ink retains the known
renderer discrepancy described in the review. Inserted note-sheet templates
are recorded but not drawn. Warnings are shown at completion and stored in
the manifest. This is an ordinary PDF with preservation attachments, not a
claim of PDF/A compliance or exact device rendering.

## First-run flow

1. **Get a backup.** On the tablet enable *Settings → Storage → USB web
   interface* (or use Wi-Fi), then run
   `remarksync pull <dest>` (USB device is `root@10.11.99.1`; the password
   is in *Settings → Help → Copyrights and licences*). The first pull
   copies everything; later pulls fetch only documents whose metadata
   changed on the tablet, using its own `ssh` and `tar` (no rsync needed).
   `--prune` also deletes documents the tablet no longer has; `--full`
   re-copies everything. Already have an rsync/scp mirror of
   `~/.local/share/remarkable/xochitl/`? Point the app at it and later
   pulls will update it in place.
2. **Open Library Folder** (Ctrl+O) and pick the backup directory.
3. **Update Search Index** (Ctrl+I) once, then search from the toolbar
   (Enter). Indexing runs in the background; click the action again to
   cancel. Later updates only touch documents that changed since the last
   run (a 66-document, 3,237-page library indexes in about 8 seconds the
   first time and skips unchanged documents instantly afterwards). "Changed"
   means the document's own timestamp *or* a text conversion the tablet
   wrote beside it, which the device does without touching that timestamp.
   Turning **Read Handwriting** on or off also counts, since it changes what
   a page's searchable text is made of, so an update after toggling it
   re-reads the library rather than skipping it.
   Scene-content hashes also notice repairs or edits that preserve metadata.
   Unreadable pages are reported; a failed document keeps its previous search
   text and is retried on the next update. CLI indexing returns a nonzero exit
   status when any document fails. Failed text retained from an earlier version
   may describe the previous revision until the page can be read again.
   **Rebuild Search Index** (Ctrl+Shift+I) re-indexes everything regardless.
   The index lives in `<backup>\.remarksync-index.db`. Clicking a result
   jumps to the page. Reading ink needs the handwriting extra, below; the
   choice is remembered.
4. **Markdown → .rmdoc** (Ctrl+M): compile any `.md` into a notebook of
   editable text pages. Upload the `.rmdoc` via the tablet's USB web UI
   (http://10.11.99.1) or drag-and-drop into the reMarkable desktop app —
   the text arrives fully editable on-device.
5. **Open .rmdoc** (Ctrl+R) views any single archive without a library.

## What works on real files — and the known limits

**Verified against a real device backup** (2026-09: 806 files, 66 documents,
3,237 pages, 288 `.rm` of which 44 are software-2.x "version=5" and 244 are
software-3.x "version=6"): notebooks, annotated PDFs and EPUBs with ink
overlay, templates, and PDF page-count fallback for unannotated documents all
render.

Placement is not taken on trust. The desktop app keeps its own render of each
page it syncs, and `tests/real` compares ours against it page by page: over
40 v6 pages our ink mass is 1.04x the device's and its centre of mass sits
7.9 tablet pixels from where the device puts it, on a 1404-pixel-wide page
(see `docs/adr/0001-page-coordinates-follow-the-tablet.md`). Ink stays where
the tablet put it and the canvas grows around the paper when ink runs past
its edges.

Real-data fixes so far: v5 pages are read as the structure their header
declares — a per-line prefix firmware ~2.13 added is what made the classic
spec look wrong, and all 44 v5 pages parse that way and account for every
byte, with the old tolerant scanner kept only for torn files; v5 strokes are
drawn at the width stored on each point rather than the pen's thickness
setting, which had them rendering at 0.39 of the device's ink; and PDFs with
empty page lists show their pages from the base PDF itself.

Known gaps, measured rather than assumed (2026-09-18, 57 inked pages): ink
mass is within 15% of the device's on every page but one — v5 spans 1.00 to
1.18 and v6 0.99 to 1.15, both with a median of 1.03 — and ink lands a median
5.4 px from where the device puts it.

What is left is not ink mass but framing, on three pages of the corpus whose
ink starts *above* the paper top. The tablet's rule there turns out to be
conditional, and we only have half of it: a page whose content stays within
the paper height is exported at exactly paper height with the ink above the
top clipped (Quick sheets p3, ink from y = -84), which is what the layout
does — but a page that *also* runs past the bottom is exported taller and
keeps that ink, which the layout does not. Its own export of one such page
carries a heading at y = -195 that we drop. Those pages are the parity
outliers (ink centroid 143 to 235 px out, almost no ink in common); the
remaining 53 sit between 0.75 and 0.95 overlap.

Templates outside the five patterns below are not drawn at all, and the ruled
ones do not distinguish their spacings — "P Lines medium" and "P Lines small"
draw the same rule.

Things to sanity-check against your own tablet backup on first use:

- **Typed text** is laid out with a model fitted against the tablet's own
  PDF exports of two typed pages, one using every paragraph style (see
  `docs/adr/0001-page-coordinates-follow-the-tablet.md` and
  `textmetrics.py`): fonts, baselines, wrapping, indents, list markers and
  checkboxes match the device to within a pixel, and ink anchored to a text
  block's lines lands where the device puts it. Wrapping uses per-glyph
  advances measured from the exports, and breaks inside a word when the
  word alone is wider than the column, which is what the device does.

  The advance table covers **every letter and digit in both faces**, each
  measured from the device's own rendering of a paragraph of it
  (`tests/real/advances.py`). Two further facts came out of the same method
  and finish the wrapping model: text stops a fixed **8 px short** of the
  column's declared width, and a **ligature is one indivisible glyph** — the
  serif face sets seven f-pairs as one, the sans only `fi` and `fl`, and the
  device ends a line early rather than split one
  (`tests/real/ligatures.py`). Punctuation is measured too, in both faces —
  the serif had none of it, so a heading with a comma in it used to wrap on an
  average twice the comma's real width. Against the tablet's own export, every
  line of a compiled document and of a three-width worksheet now breaks where
  `textmetrics` says (34 of 34 and 20 of 20).

  What is left is **kerning**, which the model does not do: the device's
  advance is really a number per *pair*, and the serif steps 0.237 em from one
  comma to the next but 0.248 from a comma to a letter. Every advance recorded
  is the step onto an ordinary letter, which is what running text asks for; the
  residue is up to 0.06 em, about 4 px in a heading, so it takes several of
  them on one line to move a break.

  The three column widths the device offers are **740, 936 and 1152** px, each
  centred on the page, and a text block records the one it was given;
  `tests/real/text_width.py` checks our wrapping at all three. The toolbar's Subheading and Bold
  share a style code and differ only in format fields rmscene skips;
  `rmformat.py` reads and writes those, so both round-trip. Fonts are
  stand-ins (Georgia, Segoe UI) for the device's own.
- **Base PDF pages** are drawn where the tablet draws them: at a fixed
  3.153 tablet pixels per PDF point, centred horizontally, top at the
  page top (physical size on the tablet's 157 mm screen; see
  `docs/adr/0002-base-pdf-pages-at-the-tablets-fixed-scale.md`). This was
  fitted from the tablet's own snap-highlight rectangles on three PDFs to
  about a pixel. US Letter pages therefore come out wider than the
  screen, and the canvas grows to show them whole.
- **The compiler was round-tripped through the tablet** on 2026-09-18
  (software 3.27, Paper Pro): the archive uploaded through the USB web
  interface (HTTP 201), came back from the tablet's own PDF export as three
  pages of real text — words, ligatures, not an image — at the paper the
  archive declares, with our pagination preserved and no paragraph split
  across a break. **All 34 lines broke exactly where `textmetrics`
  predicted**, comparing glyph by glyph rather than word by word, so that a
  ligature the export cannot name counts as the difference it is.
  `python -m tests.real.compile_roundtrip` does this end to end.

  **The compiler targets your device's page.** Every reMarkable understands
  1404x1872, and that is still the default, but a Paper Pro's page is
  1620x2160 — a sixth taller. The GUI compiles for whatever tablet wrote the
  open library, and the CLI takes `--library DIR` to work it out or
  `--paper 1620x2160` to say. A page records its own size, so the backup is
  what knows; where a library holds both (one tablet having replaced another)
  the larger wins. Verified on the device 2026-09-18: a fixture that needs
  three 1872 pages needs two at 2160, the tablet exports them at exactly the
  declared size, and all 34 lines still break where predicted.
- **.rmdoc produced by `compile`** follows the software-3.x layout
  (`<uuid>.content` with formatVersion 2, `<uuid>.metadata`,
  `<uuid>/<page-uuid>.rm` with a SceneInfo block) and imports through the
  desktop app as of 2026-09. Markdown block markers become the tablet's
  own paragraph styles: `#` a heading, `##` a subheading, `###` and deeper a bold line, `-`
  a bullet (indented: nested), `1.` a numbered item (indented: nested),
  `- [ ]` and `- [x]` checkboxes; the text arrives styled and editable.
  Lines are never wrapped by the compiler: the tablet wraps typed text
  itself, so hard-wrapped Markdown paragraphs are joined first. Each page
  holds about one page of text: each page is laid out as it fills and closed
  when the next paragraph would not fit, so a heading costs what a heading
  actually costs. A long paragraph is never split across pages.
- **Search covers** document names, typed text, PDF-highlight text, and
  handwriting three ways. Pages converted to text on the tablet are
  indexed for free: "add as new page" makes a typed page, and a shared
  conversion leaves a file beside the document. For the rest,
  `remarksync index DIR --handwriting` runs TrOCR, Microsoft's free
  handwriting model, locally: install `pip install -e .[handwriting]`
  (PyTorch with CUDA first, from the PyTorch index, for GPU speed; the
  model downloads once, about 1.3 GB). Measured against the tablet's own
  MyScript reading of a 532-word journal page it finds 80% of the words,
  at a tenth of a second per written line on an RTX 4060 Ti. Lines are
  cut from the strokes by geometry (`inklines.py`). Results are cached by
  the ink's hash in `<backup>\.remarksync-recognition.db`, so a page is
  read once. The Windows ink recogniser is available as an alternative
  engine (`--engine windows-ink`, `.[windows-ink]` extra) but it fails or
  stalls on long pages, so TrOCR is the default.
- **Pens** draw at the per-point width the tablet stored (pressure, tilt
  and thickness already applied) and in the per-line colour software 3.x
  writes (six highlighter tints, the shader's grey at 25%); this needs
  `rmscene >= 0.8`. Checked against the tablet's own PDF export of a test
  page with every pen: ink mass per pen matches within a few percent
  (pencils and paintbrush are drawn lighter than solid ink, the marker's
  chisel tip lays down 85% of its stored width; see `PEN_OPACITY` and
  `PEN_WIDTH_FACTOR` in `rmdata.py`). What is not modelled is texture:
  the tablet's pencil is grainy where ours is a smooth grey, and its
  marker has a chisel edge where ours is rounded.
- Fonts: typed text renders with Georgia and Segoe UI standing in for
  the device's serif and sans faces. (On font-less/offscreen systems text
  shows as boxes — that's environmental, not a bug.)

## Architecture

`CONTEXT.md` is the glossary; the modules below use its words.

```
src/remarksync/
  backup.py      scan a backup folder / extract an archive → FolderItem/DocItem/PageRef tree
  library.py     Library: an opened backup folder — tree, lookup by uuid, index refresh, search
                 (the window and the CLI are its two callers)
  rmdata.py      page decoder: .rm bytes → PageModel in page coordinates
                 (v5 and v6 adapters behind one decode_page)
  layout.py      page frame: PageModel → canvas size, paper rect, template or
                 underlay, dividers — every layout rule, no Qt. Also where
                 decode meets lay out (decode_frame), so a worker thread can
                 build a frame without touching Qt
  textmetrics.py the tablet's text layout: paragraph styles, fonts, glyph advances,
                 baselines and wrapping (shared by decoder, compiler and painter)
  search.py      SearchIndex: SQLite FTS5, per-document refresh keyed on
                 a document's signature and the extractor set, extractor seam,
                 prefix-AND queries with snippets
  mdcompile.py   Markdown → paginated native text pages → .rmdoc zip
  viewer.py      Qt painter: draws a PageFrame (underlay, template, highlights,
                 ink, text, dividers); computes no geometry
  gui.py         main window: tree, viewer, search dock, toolbar actions,
                 background workers for indexing and page decoding
  pull.py        incremental backup pull over ssh+tar: changed documents only, with a
                 local-directory adapter for tests
  backupstore.py retained raw revisions, writer lock, publication journal and rollback
  cloud.py / cloudcredentials.py  pinned-client acquisition and protected account sessions
  cloudsync.py   whole-library cloud polling, immutable archives and preservation renderings
  serversync.py  verified central-server downloads; no account pairing or local PDF export
  server.py      read-only private index/status/manifest and current artifact routes
  cloudsync_dialog.py / cloud_worker.py  selected-source GUI polling and child-process work
  pdfexport.py / pdfexport_worker.py  original-PDF preservation and process isolation
  inkgeometry.py / notelayout.py  shared ink outlines and optional typed-note column layout
  recognize.py   handwriting as searchable text: the tablet's own conversions, the
                 TrOCR and Windows-ink engines behind one seam, the recognition cache
  inklines.py    cut a page's strokes into written lines and render them for an engine
```

The pipeline for one page is decode → lay out → paint, and each step is a
plain function of the previous one's output. Tests exercise the decoder
and the layout without Qt or real files; the offscreen GUI smoke tests
cover the window, including background indexing.

Data policy: local viewing/search/compilation operate on preserved copies.
The selected cloud workflow downloads tablet data from reMarkable Cloud to
the private server and then to this PC over private Tailscale HTTPS. Automatic sync
does not upload, edit or delete cloud documents. Account registration/renewal
uses authentication requests; manual official-app/USB imports and explicitly
invoked writing experiments can upload documents. Cloud sending inside
RemarkSync remains proposed. See [data and repository hygiene](docs/security-and-data.md).

Viewing, searching and compiling only **read** your backup, apart from two
files they create inside it: the search index
(`.remarksync-index.db`) and the handwriting cache
(`.remarksync-recognition.db`), each with SQLite's `-wal` and `-shm`
companions. `pull` **writes** the selected SSH backup from the tablet; cloud
acquisition and server downloads write their own separate stores. Pull
stages into `.remarksync-pull-tmp/`, keeps
previous raw revisions in `.remarksync-history/`, and uses
`.remarksync-transactions/` plus `.remarksync-pull.lock` for recovery and writer
coordination. With `--prune` it removes documents from the visible backup while
retaining the preceding raw revision in history. An unfinished transaction must
be recovered by a normal pull before opening or indexing that backup.

PDF source-page mappings are retained separately from the tablet's page order,
including inserted and deleted pages. Snapped highlights retain colors and
layer visibility. All PDF access in the GUI runs on its main thread; the page
decoder worker receives only plain page sizes. PDF raster caching is bounded.
Unreadable pages display an error instead of appearing silently blank.

## Development

```bash
.venv\Scripts\python -m pytest -q            # the synthetic suite
.venv\Scripts\python -m ruff check src tests scripts deploy/server
.venv\Scripts\python scripts/check-repository-hygiene.py --history
```

Ruff runs Pyflakes rules only (`select = ["F"]`). That is deliberate: it
reports what is actually dead or broken and never argues about style, so it
stays worth reading. Before it existed, seven unreachable symbols had
accumulated unnoticed.

The synthetic suite covers scan/decode/layout/search/compile, recoverable
acquisition, PDF preservation, protected credentials, HTTP downloads, retained
history and offscreen GUI behavior. Fixtures need no tablet or personal backup.
GUI tests isolate settings so they cannot start the user's real automatic sync.
Current dated counts and outstanding real-data limits are in
[current status](docs/current-status.md). Script purposes and effects are in
[scripts/README.md](scripts/README.md).

A second, opt-in suite checks the renderer against real data:

```bash
$env:REMARKSYNC_REAL_BACKUP   = "C:\Backups\reMarkable\raw"
$env:REMARKSYNC_DESKTOP_STORE = "$env:APPDATA\remarkable\desktop"
.venv\Scripts\python -m pytest tests/real -q
```

The reMarkable desktop app keeps its own render of every page it has synced,
so `tests/real` pairs those to the same pages in a backup and asserts our
ink lands in the same place with the same weight. It is read-only, and it
skips unless both variables are set. `python -m tests.real.characterize
parity` prints the measured distributions the thresholds come from.

A second oracle compares against PDFs the tablet exports over its USB web
interface, which unlike the thumbnails reveal the size of the canvas:

```bash
.venv\Scripts\python -m tests.real.device_pdf freshness   # is the backup still current?
.venv\Scripts\python -m tests.real.device_pdf fetch       # read-only GET per document
```

**Run the real suites after any change to `rmdata.py`, `layout.py`,
`textmetrics.py` or `viewer.py`.** The synthetic tests cannot catch format
mistakes, because their fixtures encode what we already believe: two bugs
lived in this codebase for its whole life — v5 erase strokes painted over
handwriting, and every Paper Pro page laid out on the 1404x1872 sheet — with
the whole synthetic suite passing. The device is the only arbiter that can
disagree with us.

Architecture decisions live in `docs/adr/`.

## Research notes

`docs/research/rmdoc-viewer-windows.md` — the 2026 tooling landscape this
project was designed against (RCU, rmc/rmscene, pdf2rmnotebook,
rmOneNoteSync, file-format facts, the ink↔text sync constraint).

`docs/research/system-review-2026-10-03.md` records the whole-system review;
`docs/research/implementation-log-2026-10-03.md` preserves the dated fix/trial
sequence. `docs/research/review-fixes-and-feature-roadmap.md` lists remaining
work; `docs/current-status.md` is the authoritative current snapshot.

## Configuring your own installation

No address, host or account detail is built into the package. Set what your
installation needs in the environment:

| Variable | What it is |
| --- | --- |
| `REMARKSYNC_SERVER` | Your private server's HTTPS address, used by `server-sync` and offered in the GUI field |
| `REMARKSYNC_SSH_HOST` | `user@host` for the deployment helpers in `scripts/` |
| `REMARKSYNC_SSH_KEY` | Dedicated deployment key; defaults to ignored `.tools/deploy-ssh/id_ed25519` |
| `REMARKSYNC_RELEASE` | The remote release directory holding `deploy/server/compose.yaml` |

The helpers refuse to run without them rather than guessing at a deployment.
The server side has its own credential variables; see
[settings](docs/settings.md). Local viewing, search, compilation and PDF
preservation need none of this.

## Status, protocol and license

This is one person's working tool, published because it may be useful, not a
packaged product: expect to read the code and the docs rather than an installer.
The cloud side speaks reMarkable's undocumented sync protocol through a
community client, which is the same footing as rmapi and rmfakecloud — it is not
sanctioned by reMarkable, it has been changed by them before without notice, and
acquisition here is deliberately read-only. Your account and your documents are
your responsibility.

MIT licensed; see [LICENSE](LICENSE). The separately distributed `ddvk/rmapi`
client is AGPL and is downloaded at your request rather than bundled.
