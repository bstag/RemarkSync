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
.venv\Scripts\python -m remarksync pull C:\Backups\reMarkable\raw   # pull backup from tablet (USB)
.venv\Scripts\python -m remarksync scan  C:\Backups\reMarkable\raw  # print document tree
.venv\Scripts\python -m remarksync compile notes.md notes.rmdoc     # Markdown → .rmdoc
```

## First-run flow

1. **Get a backup.** On the tablet enable *Settings → Storage → USB web
   interface* (or use Wi-Fi), then run
   `remarksync pull <dest>` (USB device is `root@10.11.99.1`; the password
   is in *Settings → Help → Copyrights and licences*). Already have an
   rsync/scp mirror of `~/.local/share/remarkable/xochitl/`? Skip this.
2. **Open Library Folder** (Ctrl+O) and pick the backup directory.
3. **Update Search Index** (Ctrl+I) once, then search from the toolbar
   (Enter). Indexing runs in the background; click the action again to
   cancel. Later updates only touch documents that changed since the last
   run (a 56-document, 3,100-page library indexes in about 8 seconds the
   first time and skips unchanged documents instantly afterwards).
   **Rebuild Search Index** (Ctrl+Shift+I) re-indexes everything. The index
   lives in `<backup>\.remarksync-index.db`. Clicking a result jumps to the
   page.
4. **Markdown → .rmdoc** (Ctrl+M): compile any `.md` into a notebook of
   editable text pages. Upload the `.rmdoc` via the tablet's USB web UI
   (http://10.11.99.1) or drag-and-drop into the reMarkable desktop app —
   the text arrives fully editable on-device.
5. **Open .rmdoc** (Ctrl+R) views any single archive without a library.

## What works on real files — and the known limits

**Verified against a real 210-file device backup** (mix of software-2.x
"version=5" and software-3.x "version=6" pages, 50k+ strokes): notebooks,
annotated PDFs and EPUBs with ink overlay, templates, and PDF page-count
fallback for unannotated documents all render. Real-data fixes so far:
a tolerant v5 `.lines` scanner (the format gained extra per-line records
around firmware 2.13 that the classic spec doesn't describe — we validate
each line hard and skip four bytes past anything unrecognised), PDFs with
empty page lists show their pages from the base PDF itself, and page
placement was checked against the reMarkable desktop app (see
`docs/adr/0001-page-coordinates-follow-the-tablet.md`): ink stays exactly
where the tablet put it and the canvas grows around the paper when ink runs
past its edges.

Things to sanity-check against your own tablet backup on first use:

- **Typed text** is placed with numbers fitted against the tablet's own
  PDF export of a typed page (line height 69.3 px, first line 60 px below
  the block position, see `docs/adr/0001-page-coordinates-follow-the-tablet.md`).
  Only the plain paragraph style was measured; heading, bullet and checkbox
  heights are rmc's values, so a page mixing those styles with anchored ink
  may drift slightly. The constants live in `textmetrics.py`.
- **Base PDF pages** are drawn where the tablet draws them: at a fixed
  3.153 tablet pixels per PDF point, centred horizontally, top at the
  page top (physical size on the tablet's 157 mm screen; see
  `docs/adr/0002-base-pdf-pages-at-the-tablets-fixed-scale.md`). This was
  fitted from the tablet's own snap-highlight rectangles on three PDFs to
  about a pixel. US Letter pages therefore come out wider than the
  screen, and the canvas grows to show them whole.
- **.rmdoc produced by `compile`** is minimal (`.metadata`, `.content`,
  `pagedata`, `0/0.rm`, `0/metadata.json`). If a tablet firmware rejects
  it, compare with a real device-exported `.rmdoc` and extend
  `mdcompile.build_rmdoc`. Pages are filled by a fixed line count; a
  height budget that lets headings cost more is a one-line switch in
  `textmetrics.lines_per_page`.
- **Search covers** document names, typed text, and PDF-highlight text.
  Raw handwriting is not indexed yet — an OCR pass plugs in as a second
  *extractor* on the search index (see `search.py`) feeding the same
  FTS5 table.
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
- Fonts: text blocks render with Segoe UI. (On font-less/offscreen
  systems text shows as boxes — that's environmental, not a bug.)

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
  textmetrics.py line heights, text-box defaults, painter font size (shared by
                 decoder, compiler and painter)
  search.py      SearchIndex: SQLite FTS5, per-document refresh keyed on
                 lastModified, extractor seam, prefix-AND queries with snippets
  mdcompile.py   Markdown → paginated native text pages → .rmdoc zip
  viewer.py      Qt painter: draws a PageFrame (underlay, template, highlights,
                 ink, text, dividers); computes no geometry
  gui.py         main window: tree, viewer, search dock, toolbar actions,
                 background index worker
  pull.py        scp-based raw backup pull from the tablet
```

The pipeline for one page is decode → lay out → paint, and each step is a
plain function of the previous one's output. Tests exercise the decoder
and the layout without Qt or real files; the offscreen GUI smoke tests
cover the window, including background indexing.

Data policy: RemarkSync only **reads** your backup directory (plus one
index file it creates there). Nothing is uploaded anywhere.

## Development

```bash
.venv\Scripts\python -m pytest tests/ -q
```

54 tests cover the scan → decode → lay out → search → compile chain,
including authoring `.rm` fixtures with rmscene's writer, every page-frame
rule, index refresh and cancellation, and offscreen GUI smoke tests.

Architecture decisions live in `docs/adr/`.

## Research notes

`docs/research/rmdoc-viewer-windows.md` — the 2026 tooling landscape this
project was designed against (RCU, rmc/rmscene, pdf2rmnotebook,
rmOneNoteSync, file-format facts, the ink↔text sync constraint).
