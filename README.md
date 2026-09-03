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
3. **Rebuild Search Index** (Ctrl+I) once, then search from the toolbar
   (Enter). Clicking a result jumps to the page. The index lives in
   `<backup>\.remarksync-index.db`.
4. **Markdown → .rmdoc** (Ctrl+M): compile any `.md` into a notebook of
   editable text pages. Upload the `.rmdoc` via the tablet's USB web UI
   (http://10.11.99.1) or drag-and-drop into the reMarkable desktop app —
   the text arrives fully editable on-device.
5. **Open .rmdoc** (Ctrl+R) views any single archive without a library.

## What works on real files — and the known limits

**Verified against a real 210-file device backup** (mix of software-2.x
"version=5" and software-3.x "version=6" pages, 50k+ strokes): notebooks,
annotated PDFs with ink overlay, templates, and PDF page-count fallback for
unannotated documents all render. Two real-data fixes came out of that:
a tolerant v5 `.lines` scanner (the format gained extra per-line records
around firmware 2.13 that the classic spec doesn't describe — we validate
each line hard and skip four bytes past anything unrecognised), and PDFs
with empty page lists now show their pages from the base PDF itself.

Things to sanity-check against your own tablet backup on first use:

- **Text-block coordinates** are interpreted as centre-relative, y-down.
  If typed text shows in the wrong place on your files, adjust
  `PageCanvas._paint_texts` in `viewer.py` (one-line flip).
- **.rmdoc produced by `compile`** is minimal (`.metadata`, `.content`,
  `pagedata`, `0/0.rm`, `0/metadata.json`). If a tablet firmware rejects
  it, compare with a real device-exported `.rmdoc` and extend
  `mdcompile.build_rmdoc`.
- **Search covers** document names, typed text, and PDF-highlight text
  (GlyphRange). Raw handwriting is not indexed yet — the planned extension
  is an OCR pass (e.g. remarkdown-style geometry-aware OCR) feeding the
  same FTS5 table.
- **EPUB base documents** render without their underlay (ink only).
- Fonts: text blocks render with Segoe UI. (On font-less/offscreen
  systems text shows as boxes — that's environmental, not a bug.)

## Architecture

```
src/remarksync/
  library.py    scan xochitl backup / extract .rmdoc → FolderItem/DocItem/PageRef tree
  rmdata.py     .rm v6 → PageModel (strokes, text blocks, PDF highlights);
                pen colours, widths, highlighter/eraser semantics
  render-free: rmscene does binary parsing; rmdata is our view of it
  search.py     SQLite FTS5 index + prefix-AND query with snippets
  mdcompile.py  Markdown → paginated native text pages → .rmdoc zip
  viewer.py     Qt page canvas: templates, PDF underlay (PyMuPDF), ink, text
  gui.py        main window: tree, viewer, search dock, toolbar actions
  pull.py       scp-based raw backup pull from the tablet
```

Data policy: RemarkSync only **reads** your backup directory (plus one
index file it creates there). Nothing is uploaded anywhere.

## Development

```bash
.venv\Scripts\python -m pytest tests/ -q
```

18 tests cover the scan → parse → search → compile chain, including
authoring `.rm` fixtures with rmscene's writer and an offscreen GUI smoke
test.

## Research notes

`docs/research/rmdoc-viewer-windows.md` — the 2026 tooling landscape this
project was designed against (RCU, rmc/rmscene, pdf2rmnotebook,
rmOneNoteSync, file-format facts, the ink↔text sync constraint).
