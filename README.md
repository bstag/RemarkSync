# RemarkSync

A local-first library, viewer, search index and format compiler for
reMarkable tablets — everything works from your own raw backups, no
reMarkable Cloud required.

```
reMarkable ──scp/ssh (USB or Wi-Fi)──►  backup folder ("xochitl mirror")
                                            │
                                    RemarkSync app
                                    ├── document tree + page viewer
                                    │     (ink, colors, highlighter, layers,
                                    │      templates, base-PDF underlay)
                                    ├── full-text search (SQLite FTS5)
                                    ├── Markdown → .rmdoc compiler
                                    └── .rmdoc viewer (single archives)
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
```

Flags worth knowing: `pull --dry-run` reports what would be fetched without
writing anything, `--full` re-copies every document and `--prune` deletes
local documents the tablet no longer has; `index --rebuild` re-indexes from
scratch and `--engine` picks the handwriting engine; `compile --title` names
the notebook the tablet shows (it defaults to the Markdown file's name).

In the window, F5 rescans the open library and Ctrl+plus / Ctrl+minus zoom
the page view.

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

Known gaps, measured rather than assumed: a handful of v5 pages still draw
1.5x to 3x the device's ink, all of them carrying wide strokes, and two
stroke-heavy v6 pages draw about 1.37x. Templates outside the five patterns
below are not drawn at all.

Things to sanity-check against your own tablet backup on first use:

- **Typed text** is laid out with a model fitted against the tablet's own
  PDF exports of two typed pages, one using every paragraph style (see
  `docs/adr/0001-page-coordinates-follow-the-tablet.md` and
  `textmetrics.py`): fonts, baselines, wrapping, indents, list markers and
  checkboxes match the device to within a pixel, and ink anchored to text
  lines lands where the device puts it. Wrapping uses per-character glyph
  advances measured from the exports. The toolbar's Subheading and Bold
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
- **.rmdoc produced by `compile`** follows the software-3.x layout
  (`<uuid>.content` with formatVersion 2, `<uuid>.metadata`,
  `<uuid>/<page-uuid>.rm` with a SceneInfo block) and imports through the
  desktop app as of 2026-09. Markdown block markers become the tablet's
  own paragraph styles: `#` a heading, `##` a subheading, `###` and deeper a bold line, `-`
  a bullet (indented: nested), `1.` a numbered item (indented: nested),
  `- [ ]` and `- [x]` checkboxes; the text arrives styled and editable.
  Lines are never wrapped by the compiler: the tablet wraps typed text
  itself, so hard-wrapped Markdown paragraphs are joined first. Each page
  holds about one screen of text, charged per paragraph with the room the
  tablet gives it; a long paragraph is never split across pages.
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
                 underlay, dividers — every layout rule, no Qt
  textmetrics.py the tablet's text layout: paragraph styles, fonts, glyph advances,
                 baselines and wrapping (shared by decoder, compiler and painter)
  search.py      SearchIndex: SQLite FTS5, per-document refresh keyed on
                 lastModified, extractor seam, prefix-AND queries with snippets
  mdcompile.py   Markdown → paginated native text pages → .rmdoc zip
  viewer.py      Qt painter: draws a PageFrame (underlay, template, highlights,
                 ink, text, dividers); computes no geometry
  gui.py         main window: tree, viewer, search dock, toolbar actions,
                 background workers for indexing and page decoding
  pull.py        incremental backup pull over ssh+tar: changed documents only, with a
                 local-directory adapter for tests
  recognize.py   handwriting as searchable text: the tablet's own conversions, the
                 TrOCR and Windows-ink engines behind one seam, the recognition cache
  inklines.py    cut a page's strokes into written lines and render them for an engine
```

The pipeline for one page is decode → lay out → paint, and each step is a
plain function of the previous one's output. Tests exercise the decoder
and the layout without Qt or real files; the offscreen GUI smoke tests
cover the window, including background indexing.

Data policy: **nothing is ever uploaded anywhere.** Everything runs on your
machine against your own files.

Viewing, searching and compiling only **read** your backup, apart from two
files they create inside it: the search index
(`.remarksync-index.db`) and the handwriting cache
(`.remarksync-recognition.db`), each with SQLite's `-wal` and `-shm`
companions. `pull` is the one command that **writes** to the backup: it is
what fills it from the tablet, it stages into `.remarksync-pull-tmp/`, and
with `--prune` it also **deletes** local documents the tablet no longer has.

## Development

```bash
.venv\Scripts\python -m pytest tests/ -q
```

103 tests (2026-09) cover the scan → decode → lay out → search → compile
chain, including authoring `.rm` fixtures with rmscene's writer, every
page-frame rule, index refresh, cancellation and schema migration, and
offscreen GUI smoke tests. All of them are synthetic, so they need no tablet
and no backup.

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

Architecture decisions live in `docs/adr/`.

## Research notes

`docs/research/rmdoc-viewer-windows.md` — the 2026 tooling landscape this
project was designed against (RCU, rmc/rmscene, pdf2rmnotebook,
rmOneNoteSync, file-format facts, the ink↔text sync constraint).
