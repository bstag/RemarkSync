# Windows options for viewing reMarkable notes (.rm / .rmdoc)

Researched 2026-09-02. Primary sources cited inline. Items marked **(unverified)** were not confirmed against source and should be checked before relying on them.

## TL;DR

1. **Buy RCU ($12, [davisr.me/projects/rcu](https://www.davisr.me/projects/rcu/))** — it is the only maintained, Windows-native GUI today that snapshots your whole device *and* renders/export notebooks (vector PDF, layer toggles, colors, highlight extraction). Supports RM1/RM2/Paper Pro/Move/Paper Pure, software 1.8–3.27, USB or Wi-Fi. v5.1.1 released Aug 2026 — actively maintained. AGPLv3; $12 buys binaries + source + 1 year of updates.
2. **There is no maintained open-source GUI viewer for modern (v6/color) notebooks on Windows.** The closest, [ReMy](https://github.com/bordaigorl/remy), explicitly does not support tablet software 3.x / the v6 `.rm` format. That gap is exactly what a custom "RemarkSync viewer" would fill.
3. **To build one:** fastest path is Python (`rmscene` parser + PySide6 GUI) pointed at your existing raw xochitl backup; a distributable single-exe app would be Rust (`remarkable-lines` crate + egui) or Tauri with a Kaitai-generated JS/WASM parser. `.rmdoc` is just a zip of the document's UUID directory, so "open .rmdoc" is nearly free once you can read `.rm`.

---

## 1. File-format ground truth

### On-device storage (RM2 and Paper Pro)

- Documents live under `/home/root/.local/share/remarkable/xochitl/` — still true on the Paper Pro ([remarkable.jms1.info filesystem docs](https://remarkable.jms1.info/), [Reddit](https://www.reddit.com/r/RemarkableTablet/comments/fx5c0f/syncing_files_between_remarkable_and_computer/)). Each document/folder is a UUID directory containing:
  - `.metadata` — JSON: `visibleName`, `parent`, `type`, timestamps
  - `.content` — page list + per-page template references
  - `pagedata` — per-page template assignments
  - per-page numbered dirs `0/`, `1/`, … each holding `N.rm` (binary strokes) + `metadata.json`
- SSH access on the Paper Pro: enable the USB web interface in **Settings → Storage**, device appears at `10.11.99.1` ([informaticar.net guide](https://www.informaticar.net/how-to-connect-to-remarkable-paper-pro-via-ssh/)); developer mode grants full-filesystem SSH ([developer.remarkable.com](https://developer.remarkable.com/documentation/developer-mode)). The USB web UI (`http://10.11.99.1/`) handles file transfer without cloud ([lizard-spock.co.uk](https://lizard-spock.co.uk/remarkable-local-file-transfer-without-the-cloud.html)).

### `.rm` stroke format versions

| Version | Tablet software | Notes |
|---|---|---|
| `.lines` v3/v4 | pre-2.x | legacy |
| v5 | software 2.x | one page per `.rm` file ([akeil/rmtool](https://github.com/akeil/rmtool)) |
| **v6** | **software 3.x (2023→current)** | adds typed-text blocks and per-line color (`color_rgba`, incl. separate highlighter color) — needed for Paper Pro colors ([ricklupton/rmscene](https://github.com/ricklupton/rmscene), [r/RemarkableTablet v6 thread](https://www.reddit.com/r/RemarkableTablet/comments/10bbteg/support_for_remarkable_lines_version6_file_format/)) |

Original binary layout (header → layers → lines → points with pressure/tilt/etc.): [plasma.ninja reverse-engineering writeup](https://plasma.ninja/blog/devices/remarkable/binary/format/2017/12/26/reMarkable-lines-file-format.html). Kaitai Struct spec: [matomatical/reMarkable-kaitai](https://github.com/matomatical/reMarkable-kaitai) (has a live JSFiddle that parses + renders an uploaded `.rm` — proof a webview UI is viable).

### `.rmdoc` container

- `.rmdoc` = a **ZIP of one document's UUID directory** (`.content`, `.metadata`, page dirs with `.rm` + `metadata.json`, `pagedata`) — i.e., the xochitl structure, packaged ([remarkable.jms1.info](https://remarkable.jms1.info/), [Andrew Doering's blog via search](https://andrew.doering.org/blog/) **(unverified link)**).
- Introduced as the native share/import format with software 3.x. Drag-and-drop a `.rmdoc` into the reMarkable desktop app (or USB web UI) and it uploads to the tablet, where it arrives **fully editable with layers intact** ([r/RemarkableTablet](https://www.reddit.com/r/RemarkableTablet/comments/1rikqvb/still_wondering_how_to_use_rmdoc_files_read_this/)). This round-trip is what makes a custom *editor* that outputs `.rmdoc` feasible.

### Official reMarkable desktop app (Windows)

Cloud-mediated: viewing/organizing works with limited sync; **taking/editing notes in the desktop app requires a Connect subscription** ([support.remarkable.com Desktop app](https://support.remarkable.com/s/article/Desktop-app), [Using without a subscription](https://support.remarkable.com/s/article/Using-reMarkable-without-a-subscription)). Importing a `.rmdoc` uploads it — the app does **not** render `.rmdoc` locally. So the official app is not an offline viewer for backups.

---

## 2. Existing software

| Tool | Input | Output / capability | Windows GUI | v6/color | Maintained | License/cost |
|---|---|---|---|---|---|---|
| [RCU](https://www.davisr.me/projects/rcu/) | device via USB/Wi-Fi (RM1/2, Paper Pro, Move, Pure; SW 1.8–3.27) | whole-data snapshots + restore; notebook download as "exact replicas" restorable to any device; PDF export (vector or bitmap, layer toggles, custom ink colors); snap-highlight → TXT; typed text → Markdown; virtual printer; scriptable CLI | ✅ | ✅ | ✅ v5.1.1 (2026-08) | AGPLv3, $12 binaries |
| [Official desktop app](https://support.remarkable.com/s/article/Desktop-app) | cloud-paired device | view/organize in-app; `.rmdoc` import = upload, not local render | ✅ | ✅ | ✅ | free; Connect sub for note editing |
| [rmscene](https://github.com/ricklupton/rmscene) + [rmc](https://github.com/ricklupton/rmc) (Python) | `.rm` v6 | parse (lines, text, colors) / convert to SVG, PDF, Markdown (CLI) | ❌ | ✅ (colors incl. highlighter `color_rgba`) | ✅ active | open source |
| [rmrl](https://github.com/rschroll/rmrl) (Python) | annotations + base PDF | annotated PDF render | ❌ | v6 status **(unverified)** | check | open source |
| [rm2pdf](https://github.com/rorycl/rm2pdf) (Go) | notebooks + annotated PDFs | A4 PDF with strokes | ❌ | v5-era; v6 **(unverified)** | check | open source |
| [ReMy](https://github.com/bordaigorl/remy) (PyQt) | SSH, rsync, or **local backup dir** | GUI tree, notebook preview, PDF export overlay | ⚠️ untested on Windows | ❌ **"Software Version 3 NOT SUPPORTED"** (issue #49) | stalled | GPL-3 |
| [RemaPy](https://github.com/peerdavid/remapy) | reMarkable cloud | explorer, upload/download, backup | ✅ | n/a (no stroke render) | ✅ | open source |
| [reMarkable Remember](https://github.com/ds160/remarkable-remember) | device/offline backups | backups, notebook viewer, MyScript handwriting recognition | ✅ | **(unverified)** | check | open source |
| [rm-exporter](https://github.com/chopikus/rm-exporter) | device | bulk export folders/notes | ❌ CLI/GUI **(unverified)** | **(unverified)** | check | open source |
| [remarks](https://github.com/Scrybbling-together/remarks) | raw files | highlights → Markdown/PNG/SVG | ❌ | **(unverified)** | check | open source |
| [reManager](https://github.com/rmitchellscott/reManager) | device | **mod/template manager (Vellum packages)** — not a note viewer/backuper | ✅ | n/a | check | open source |
| [reMarkableSync](https://github.com/jamesf91/reMarkableSync) | notebooks | OneNote add-in (imports as text/images) | ✅ (OneNote COM) | **(unverified)** | check | open source |
| [rmOneNoteSync (Excustic)](https://github.com/Excustic/rmOneNoteSync) | device via on-tablet daemon (Wi-Fi/USB; dev mode required) | **one-way** sync to OneNote via Microsoft Graph: folders→Notebooks, docs→Sections, pages→Pages, transferred as native ink (InkML), not page images | ✅ Avalonia app (Win 11 x64 tested; also macOS/Linux) | Paper Pro + Move on OS 3.26 tested; RM1/RM2 untested | ✅ active — v1.0.0-alpha.3 (Apr 2026), releases Mar–Apr 2026 | GPL-3 |

Note: "rmformat" (from the original plan) could not be found as a reMarkable project — likely a mis-remembering of the Rust crates below or of RCU. "rmapy" is listed as unmaintained on [awesome-reMarkable](https://github.com/rehackable/awesome-reMarkable); [rmapi](https://github.com/juruenegarai/rmapi) / rmcl remain the cloud-API clients.

### rmOneNoteSync in detail

[Excustic/rmOneNoteSync](https://github.com/Excustic/rmOneNoteSync) is a GPL-3.0 hobby/student project (small community, ~7 stars, 76 commits) that implements exactly the "Area 1" architecture from the original plan: a lightweight C daemon (`rm-daemon`) installed on the tablet plus a cross-platform Avalonia C# desktop app (`app`, Windows installer `rmOneNoteSyncApp-Setup.exe`). Local-first server-client: your PC talks to the tablet daemon and pushes to OneNote through the **Microsoft Graph API** (personal and business accounts supported since alpha.2) — no reMarkable Cloud involved, and no OneNote COM interop (so it targets the modern Graph-synced OneNote apps, not classic desktop add-ins).

- **Mapping:** parent folder → OneNote Notebook, document → Section, page → Page; autosync-marked folders pick up new documents automatically; manual per-page resync as fallback; multi-threaded batch upload (up to 5 concurrent Graph requests).
- **Ink fidelity:** author states it "transfers complete ink data, giving me control over every dot and letter on OneNote" (repo tags suggest InkML serialization) — i.e., native OneNote ink, not flattened printouts. Base-PDF/EPUB underlays are presumably not included (ink only).
- **State:** alpha. Releases: v1.0.0-alpha (20 Mar), alpha.2 (27 Mar, MS multi-client + manual sync + reduced SSH reliance), alpha.3 (11 Apr — multi-host, autosync for new docs). Tested on Windows 11 x64 + Arch Linux with Paper Pro and Move (rM OS 3.26.0.68); RM1/RM2 and macOS untested. Requires developer mode on the tablet; cloud sync not supported.
- **Caveats:** reads/writes internal tablet files — author advises cloud backup (a raw xochitl backup covers this better). One-way only (reMarkable → OneNote); no PDF-export or device-restore capability.

### Parser libraries worth reusing

- **Python:** [rmscene](https://github.com/ricklupton/rmscene) (v6: lines, text, colors; unknown blocks preserved as `UnreadableBlock` so parsing never hard-fails)
- **Rust:** [remarkable-lines (Lyr-7D1h)](https://github.com/Lyr-7D1h/remarkable-lines) ("lines, color, and text") and [rm-lines (crates.io)](https://crates.io/crates/rm-lines)
- **Language-agnostic:** [reMarkable-kaitai](https://github.com/matomatical/reMarkable-kaitai) — Kaitai can emit JS/Python/C++ parsers; the repo's JSFiddle already renders an uploaded `.rm`
- **C++:** [lines-are-beautiful (ax3l)](https://github.com/ax3l/lines-are-beautiful) (v5-era)

---

## 3. Building a custom Windows viewer ("RemarkSync viewer")

### Data sources (pick any/all)

1. **Local raw backup dir** (what you already have: `C:\Backups\reMarkable\raw\`) — read-only, mirrors xochitl. ReMy proved this UX.
2. **Open `.rmdoc` directly** — it's a zip; `zipfile` + the same parser and you're done.
3. **Live SSH/rsync mirror** — `rsync` over USB (`10.11.99.1`) keeps the local dir fresh.
4. **Cloud API** (optional) — via rmapi/rmcl; least valuable given your local-backup posture.

### Rendering essentials

- Strokes are polylines: per-line `color`/`base_size`/brush type, per-point `(x, y, pressure, tilt, x_speed, y_speed)`. Render as variable-width paths (pressure → width) or fixed-width simplification.
- **Highlighter** = translucent stroke with multiply/alpha blending over the base (v6 `color_rgba`).
- **Layers** are first-class in `.rm` — a viewer should get a layer toggle (RCU already proves it).
- **Backgrounds**: template names from `pagedata` (grid/dots/lines) — draw programmatically or ship the official template PDFs; annotated PDFs/EPUBs use the cached base document in the UUID dir.
- Export to vector PDF via `rmc`-style code (Python: cairo/reportlab; Rust: `printpdf`/`krilla`).

### UI stack options (Windows)

| Stack | Effort to MVP | Pros | Cons |
|---|---|---|---|
| **Python + PySide6 + rmscene** | days | parser already done; Qt GUI + `QPainterPath` vector render; PyInstaller exe | ~50 MB bundle, Python runtime smell |
| **Rust + `remarkable-lines` + egui/iced** | 1–3 weeks | single static exe, no deps, fast | GUI work is more manual; verify crate's v6 color coverage first |
| **Tauri + Kaitai-generated JS parser** | 1–3 weeks | web-tech UI, tiny binary, JSFiddle already proves parse+render in browser | WASM/parser glue; two toolchains |
| **C# WinUI3/WPF** | weeks+ | native Windows feel | needs a full v6 parser port to C# (none exists today **(unverified)**) |

### Feature ladder

1. Read-only viewer: open backup dir / `.rmdoc` → doc tree (from `.metadata`) → page render with pan/zoom. (MVP)
2. Thumbnails + search over `visibleName`; layer toggle; template backgrounds; base-PDF underlay.
3. Export to PDF (reuse rmc logic) — feeds your existing OneNote pipeline.
4. Stretch: editor that writes `.rmdoc` — tablet imports it natively with layers editable, giving true round-trip.

---

## 4. Recommendation

- **Today:** RCU for viewing/export/snapshots + keep the raw xochitl `scp`/`rsync` backup as the golden DR copy. Verify your reManager backups actually capture the full UUID trees (`.metadata`, `.content`, `.pagedata`) — reManager is a mod/template manager first, so double-check before trusting it for DR.
- **Build:** prototype in this repo with **Python + rmscene + PySide6** pointed at `C:\Backups\reMarkable\raw\`; it de-risks the format work in days. Graduate to Rust/Tauri only if you want a distributable single-exe app.

## 5. Bidirectional workflows: writing .rmdoc, annotation library, search (added 2026-09-03)

### Tools that WRITE .rm / .rmdoc (verified)

| Tool | Direction | What it writes | Status |
|---|---|---|---|
| [rmc](https://github.com/ricklupton/rmc) | **Markdown → `.rm`** | `rmc -t rm text.md -o text.rm` — text content only (no strokes) | ✅ active, MIT, `pip install rmc` |
| [Drawj2d](https://drawj2d.sourceforge.io/) | Drawing language (`.hcl`) → **`.rmdoc`/`.rmn`/`.rm`** | Lines, points, circles, ellipses, arcs, arrows, labels with fonts, pen color/width/dash; imports PDF/SVG/DXF/images as source | ✅ active (1.4.0, 2025), GPL, Java 11+ (runs on Windows) |
| [pdf2rmnotebook](https://github.com/JCN-9000/pdf2rmnotebook) | **PDF/PNG → notebook** (uses Drawj2d + pdfinfo) | Outputs `.zip` (rmapi), `.rmn` (RCU), **`.rmdoc` (directly uploadable via the tablet's USB web UI)**; pages become *native editable strokes*, not image backgrounds; `-s SCALE` (default 0.75) | ✅ maintained (V3.3.0 supports Pro/Move), GPL-3, bash script |
| [rmscene](https://github.com/ricklupton/rmscene) | experimental writer | `write_blocks` round-trip writer exists but is **not** a documented authoring API; round-trip saving preserves unknown bytes | ⚠️ experimental |

Note the two meanings of "PDF → editable": (a) reMarkable already lets you annotate any uploaded PDF (strokes live beside the PDF — no conversion needed; export via RCU); (b) pdf2rmnotebook converts PDF *content* into stroke line-work, making the content itself editable/erasable at the cost of the text layer (text becomes drawn lines).

### Search-enabling facts

- **Typed text is native in v6 `.rm`** — extractable directly via rmscene/rmc, no OCR.
- **PDF text highlights** are readable as `GlyphRange` items (rmscene v0.3.0+) — highlight text is directly searchable.
- **Handwriting** needs an HWR/OCR pass on rendered pages: [remarkdown.org's geometry-aware OCR](https://remarkdown.org/remarkable-ocr) (splits pages by stroke geometry, built so LLMs can read the notes), [Vertex AI DIY approach](https://blog.wilcoxd.com/2024/04/19/Remarkable-Handwriting-with-Vertex-AI/), commercial [HandwritingOCR](https://www.handwritingocr.com/handwriting-to-text/how-to-convert-handwriting-to-text-on-remarkable-2), or the on-device "convert to text" feature ([reMarkable support](https://support.remarkable.com/s/article/Convert-handwritten-notes-into-text)).
- Upload channel back to the tablet without cloud: the **USB web UI accepts `.rmdoc` directly** ([rmdoc thread](https://www.reddit.com/r/RemarkableTablet/comments/1rikqvb/still_wondering_how_to_use_rmdoc_files_read_this/), pdf2rmnotebook README).

### Proposed "RemarkSync" architecture (local-first hub)

```text
reMarkable ──rsync over USB/Wi-Fi──►  Local store (raw xochitl UUID dirs = golden copy)
                                        │
                              normalize + index (SQLite FTS5: names, typed text,
                              PDF-highlight text, OCR'd handwriting)
                                        │
        ┌───────────────┬───────────────┼────────────────┬───────────────┐
        ▼               ▼               ▼                ▼               ▼
   Viewer GUI      Search UI      Annotated-PDF    md → rmdoc       rmdoc → md
   (.rm render +   (FTS over       export (rmrl/    (rmc paginated   (rmc text +
    base-PDF        index)          rmc style        + zip to         OCR extract)
    underlay)                       overlay)         .rmdoc) ──upload via USB web UI
```

**Honest constraint:** ink is not text — true lossless bidirectional *merge* between Markdown and `.rmdoc` is not achievable. Workable model: markdown is the source for generated documents (re-publish regenerates pages), the tablet is the source for ink; or publish markdown text as the base layer of a notebook so tablet edits arrive as a separate ink layer, and extraction (text + OCR) is the only "sync back".

## Sources


- [RCU — Davis Remmel](https://www.davisr.me/projects/rcu/) · [FSF directory entry](https://directory.fsf.org/wiki/ReMarkable_Connection_Utility_(RCU))
- [awesome-reMarkable](https://github.com/rehackable/awesome-reMarkable) (tool index)
- [ricklupton/rmscene](https://github.com/ricklupton/rmscene) · [ricklupton/rmc](https://github.com/ricklupton/rmc) · [rmc on PyPI](https://pypi.org/project/rmc/)
- [akeil/rmtool](https://github.com/akeil/rmtool) · [rorycl/rm2pdf](https://github.com/rorycl/rm2pdf) · [rschroll/rmrl](https://github.com/rschroll/rmrl)
- [Lyr-7D1h/remarkable-lines](https://github.com/Lyr-7D1h/remarkable-lines) · [rm-lines crate](https://crates.io/crates/rm-lines) · [matomatical/reMarkable-kaitai](https://github.com/matomatical/reMarkable-kaitai) · [ax3l/lines-are-beautiful](https://github.com/ax3l/lines-are-beautiful)
- [bordaigorl/remy](https://github.com/bordaigorl/remy) · [peerdavid/remapy](https://github.com/peerdavid/remapy) · [ds160/remarkable-remember](https://github.com/ds160/remarkable-remember) · [jamesf91/reMarkableSync](https://github.com/jamesf91/reMarkableSync) · [rmitchellscott/reManager](https://github.com/rmitchellscott/reManager)
- [Excustic/rmOneNoteSync](https://github.com/Excustic/rmOneNoteSync) · [releases](https://github.com/Excustic/rmOneNoteSync/releases)
- [plasma.ninja .lines format](https://plasma.ninja/blog/devices/remarkable/binary/format/2017/12/26/reMarkable-lines-file-format.html) · [r/RemarkableTablet v6 thread](https://www.reddit.com/r/RemarkableTablet/comments/10bbteg/support_for_remarkable_lines_version6_file_format/) · [rmdoc usage thread](https://www.reddit.com/r/RemarkableTablet/comments/1rikqvb/still_wondering_how_to_use_rmdoc_files_read_this/)
- [remarkable.jms1.info filesystem docs](https://remarkable.jms1.info/) · [remarkable.guide SSH](https://remarkable.guide/guide/access/ssh.html) · [developer.remarkable.com developer mode](https://developer.remarkable.com/documentation/developer-mode)
- [informaticar.net Paper Pro SSH](https://www.informaticar.net/how-to-connect-to-remarkable-paper-pro-via-ssh/) · [lizard-spock.co.uk local transfer](https://lizard-spock.co.uk/remarkable-local-file-transfer-without-the-cloud.html)
- [reMarkable support: Desktop app](https://support.remarkable.com/s/article/Desktop-app) · [without subscription](https://support.remarkable.com/s/article/Using-reMarkable-without-a-subscription) · [importing/exporting](https://support.remarkable.com/s/article/importing-and-exporting-files)
