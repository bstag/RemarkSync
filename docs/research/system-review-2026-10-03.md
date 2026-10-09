# RemarkSync system review and cloud/PDF roadmap

Historical baseline assessment, captured before the October fixes and
deployment. Findings below describe that earlier checkout, not unresolved
current bugs. All F1–F9 have implementation/regression coverage; see
[current status](../current-status.md), the
[implementation log](implementation-log-2026-10-03.md), and
[remaining roadmap](review-fixes-and-feature-roadmap.md).

Reviewed 2026-10-03 against checkout `d85b328`. Scope: all production modules, README/glossary/ADRs, synthetic tests, real-data validation setup, and current primary-source research. This is a whole-system assessment, not a branch-diff review. No application code was changed, no tablet/account was accessed, and nothing was deployed.

## Assessment

Both requested capabilities are achievable without replacing the application. The decoder, page frame, library and search modules offer useful seams. However, the current application is a local backup reader with an explicit SSH pull command; it has neither a cloud source nor an annotated-PDF export command. Its current backup is the latest fetched state, not a versioned preservation archive.

Build accurate PDF preservation and safer storage first, prove cloud downloads with a representative document, then automate processing. Hosting the worker is only necessary if processing must continue while the PC is off. Merely copying the backup folder to cloud storage does not get new edits from the tablet.

## Architecture and coverage

| Area | Current behavior | Assessment |
| --- | --- | --- |
| Acquisition | `pull.py`: SSH + tar over USB or reachable Wi-Fi; metadata/conversion signatures select changed documents | Useful transport abstraction (`Tablet`), but replacement needs validation, recovery and locking |
| Storage | Raw document files in a backup folder; replacement of changed documents; optional pruning | Suitable input for the viewer; insufficient as the only historical archive |
| Document model | `backup.py`: folders, documents, page positions, base PDF, normalized templates | Needs stable page IDs, source-PDF mapping, deletion and ordering metadata for export |
| Page decoding | `rmdata.py`: v5/v6 scenes to strokes, text and snapped highlights | Strong existing work, but this display model loses some annotation metadata |
| Page framing | `layout.py`: paper, underlay, overflow and dividers | Shared Qt-free geometry is useful; device-width and template defects need correction |
| Viewing | `viewer.py` / `gui.py`: Qt painter, asynchronous decoding, rasterized PDF underlay | A viewer exists, but painting screenshots would discard the original PDF's text/vector structure |
| Search | SQLite FTS5, per-document transactions, extractor fingerprint, tablet conversions, optional local OCR | Good local design; errors can silently become a completed index entry; base PDF body text is not indexed |
| Compilation | Markdown paragraph styles and pagination into editable `.rmdoc` | CLI path is covered; the GUI action currently fails |
| Cloud operations | No credentials, cloud adapter, scheduling, remote API, hosted worker or web UI | New functionality, not a configuration toggle |
| Preservation export | No PDF writer or export UI/CLI | Decoder/geometry can be reused after correctness work |

Good foundations include the separation of decode → frame → paint, synchronous library operations shared by CLI/GUI, per-document search transactions, cached OCR, and real-device oracle tests. The documented practice of checking actual tablet exports is especially valuable: synthetic fixtures cannot establish proprietary-format fidelity by themselves.

## Findings, ordered by preservation risk

P1 means address before unattended acquisition or dependable preservation; P2 means a functional/correctness defect. Findings identify whether they were reproduced or follow from code/documentation.

### F1 — P1: Document replacement can remove the only good copy

`src/remarksync/pull.py:209`: after `fetch()` returns, the code removes each old document before moving staged entries into place. It does not validate that the staged document exists or is complete. A source returning an incomplete batch without raising is accepted as successful, deleting missing documents' local files. A disk/move failure after deletion also lacks rollback. The staging directory protects against an exception during fetch, not a failure during publication.

Reproduced: an adapter advertised one changed document and returned an empty staging directory. `pull()` returned `completed=True`; the existing metadata and content were gone.

Fix: validate staged metadata/content and expected payloads against a manifest/revision, retain the old revision, and publish a complete new version by switching a manifest/pointer. Use unique staging directories and a single-writer lock. Record completion only after durable publication. Keep the flat-folder adapter for existing backups if needed, but do not assume swapping several files is one atomic operation.

### F2 — P1: Tar extraction can write beyond its destination

`src/remarksync/pull.py:254` / `src/remarksync/backup.py:285`: the extraction guard checks member names but not link targets or member types; `tar.extractall()` has no explicit safe filter. Under the project's installed Python 3.13, a hard-link member named inside staging can target a file outside staging, then a regular member of the same name can overwrite that file.

Reproduced entirely in a temporary fixture: a sibling `victim.txt` changed from `original` to `overwritten` despite passing the existing member-name guard. Interpreter defaults vary; a newer interpreter's default protection does not protect every supported Python version.

Fix: allow only regular files and directories, reject links/device entries, apply an explicit safe extraction policy, impose size limits, and validate document/path identities. Keep this protection at the source-import boundary for any future connector.

### F3 — P1: Ink can be paired with the wrong original PDF page

`src/remarksync/backup.py:147` keeps page IDs/templates but discards source-page mapping and deletion fields. `src/remarksync/viewer.py:268` and `:329` use the current list position to choose the PDF page. This only works while document order matches base-PDF order and there are no inserted note pages. Reordering, deleting or inserting pages can put correct ink over the wrong page.

A synthetic mapped-document probe confirmed that no source-page reference survives into `PageRef`. Primary community parser evidence identifies modern `cPages.pages[].redir.value` and legacy `redirectionPageMap` as mapping inputs; it also handles deleted and inserted pages. Complete proprietary ordering semantics still need real fixtures. See the [source research](cloud-sync-and-pdf-preservation-sources.md#preserve-highlights-and-notes-on-a-copy-of-the-original-pdf).

Fix: retain page UUID, logical position, source-PDF index or inserted-page status, and raw mapping metadata independently. Resolve ordering/deletion explicitly, then share this mapping across viewing, search navigation and export. Never infer a missing mapping by silently drawing a different original page.

### F4 — P1: PDF operations run across threads contrary to library guidance

`src/remarksync/gui.py:73` opens a PDF in `DecodeWorker` while the main thread has its own PDF at `:298`; the main thread renders it in `viewer.py`. The comment assumes separate handles suffice. PyMuPDF's documentation says multithreaded operation is unsupported and can produce incorrect behavior or crash Python. Separate handles are not a documented exemption. [PyMuPDF multiprocessing guidance](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)

This is a documented correctness risk; a crash was not reproduced. Fix: use a separate process for PDF operations that must run concurrently, or keep all PDF access on one thread. In a cloud worker, isolate PDF jobs by process and do not share PDF objects across jobs.

### F5 — P2: GUI Markdown compilation always fails after choosing an output

`src/remarksync/gui.py:515` reads `self.library`, but the window owns `self._library` and has no `library` property. The access is also outside the `try` block.

Reproduced with isolated settings and mocked file dialogs: `AttributeError: 'MainWindow' object has no attribute 'library'`. Fix the property access and cover the actual GUI action, with and without an open library. Existing CLI/compiler tests do not exercise this path.

### F6 — P2: Unreadable pages appear blank and can stay missing from search

`rmdata.py` returns `PageModel.error` when parsing fails, but the GUI/painter never presents it. `src/remarksync/search.py:178` indexes an empty model and records the document signature as complete. A later repair that preserves `lastModified` and conversion signature is skipped.

Reproduced: index a corrupted page, replace it with valid scene data without changing metadata, rescan and refresh. The refresh skipped the document; the repaired note was absent from search. This is particularly relevant to partial restores and externally maintained backup folders.

Fix: surface page errors, count partial/failed documents separately, retry failed pages, and include immutable revision/content hashes in index freshness. An exporter should never report complete preservation while silently omitting an unreadable page.

### F7 — P2: Snapped-highlight color and visibility are lost

`src/remarksync/rmdata.py:183` stores highlight text/rectangles only; `:337` drops `GlyphRange.color` and ignores inherited visibility/anchor translation for that item. `src/remarksync/viewer.py:351` paints every snapped highlight yellow. Text blocks also lack a visibility field, whereas strokes retain one.

Reproduced: decoding a green snapped highlight yields a highlight with no color field. These are snapped text highlights, distinct from freehand highlighter strokes, whose colors are retained.

Fix: extend the export/display model to retain color, opacity, layer identity/visibility, and appropriate transforms for every item type. Preserve raw scene files even after enriching the model. Define whether an export includes visible layers only or all layers; do not silently mix those policies.

### F8 — P2: Base-PDF centering disagrees with Paper Pro page coordinates

`src/remarksync/rmdata.py:326` shifts center-relative scene coordinates by half the page's declared width (810 on Paper Pro). `src/remarksync/layout.py:103` always centers the base PDF on the fixed 1404 width (center 702). The two frames differ by 108 tablet pixels, approximately 34 PDF points using the current scale.

Confirmed by code and an isolated geometry probe, not by a new real-device export. The current fixed PDF scale has valuable measured evidence and should not be replaced with generic fit-to-screen scaling. Fix the inconsistent width input, then verify Paper Pro PDF fixtures and any device-specific transform against official/device exports.

### F9 — P2: Templates disappear on ordinary Paper Pro pages after page one

`src/remarksync/layout.py:156` permits later-page templates only when the canvas height is at most fixed `PAGE_HEIGHT=1872`. An ordinary declared Paper Pro page is 2160 tall, so it is incorrectly treated like an overflow page.

Reproduced: `lay_out(PageModel(paper_size=(1620, 2160)), template='ruled', page_index=1).template` is `None`. Fix the condition to use that page's paper height and the actual overflow policy.

## Other operational limits

- The README already documents clipping of some above-paper ink when the page also extends below the paper, approximate brush texture, limited template fidelity and substitute fonts. These are material preservation limits even if the current viewer is satisfactory. Out-of-page notes require explicit treatment rather than reusing clipping rules silently.
- SSH captures whole tar batches in memory; PDF underlay images have an unbounded per-document cache. Large documents/libraries need bounded caches, streamed downloads and job limits before unattended operation.
- Concurrent pulls share one staging directory and there is no coordination with the viewer/indexer. Readers can observe files during replacement. Immutable published snapshots address both consistency and preservation history.
- Qt worker cancellation waits up to 30 seconds on the UI thread and does not check the wait result. Long OCR jobs can delay switching/closing; a timeout must not be treated as worker completion. Process-backed jobs allow clearer cancellation and recovery.
- OCR is an aid to search, not a substitute for preserving ink. Model/version/settings belong in cache identity when recognition behavior changes. Keep PDF export independent of OCR success.
- The minimum-version dependency ranges and global `rmscene` format patches make a tested dependency snapshot useful for repeatable export. Before distributing/hosting, review actual dependency licenses; the package's MIT declaration does not describe every dependency. [PyMuPDF license information](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
- The README's statement that nothing is uploaded anywhere describes the present design. Cloud acquisition/hosting requires an explicit revised data policy and clear destinations for raw documents, originals, generated artifacts and account tokens.

## Cloud sync choices

| Option | Tablet needs a cable/direct PC connection? | PC must run? | Work required |
| --- | --- | --- | --- |
| Existing `pull --host root@<tablet-Wi-Fi-IP>` | No cable; PC must reach tablet over the network | Yes, during pull | Already supported; still a manual local pull |
| Official cloud + desktop/mobile exports | Tablet Wi-Fi and account sync | Depends on which app performs export | Available manual route |
| RemarkSync cloud adapter on PC | Tablet Wi-Fi to reMarkable Cloud; no tablet-to-PC connection | Yes, when downloading/exporting | Cloud source adapter, pairing, revision handling, safe import |
| Hosted RemarkSync worker | Tablet Wi-Fi to reMarkable Cloud; no tablet-to-PC connection | No | Adapter plus always-on host, persistent state, secrets, artifact delivery |

Official sync operates whenever the tablet has Wi-Fi. Without Connect, documents inactive for more than 50 days face sync limitations; keep historical archive versions independently and never interpret a limited cloud listing as an instruction to delete them. [Official file transfer/sync](https://support.remarkable.com/articles/Knowledge/importing-and-exporting-files), [Without a subscription](https://support.remarkable.com/articles/Knowledge/Using-reMarkable-without-a-subscription)

The practical automated raw-download candidate is the maintained [ddvk/rmapi client](https://github.com/ddvk/rmapi). It supports unattended commands and raw `.rmdoc` acquisition. This is a community integration; no vendor-supported public raw-document SDK was found in the checked sources. Pin a tested revision and validate downloaded documents against USB snapshots before relying on it. Do not use the old archived upstream, and do not assume rmapi's basic annotated-PDF renderer meets this project's pen/format requirements.

Recommended target:

```text
Tablet --Wi-Fi--> reMarkable Cloud
                       |
                 read-only acquisition worker
                       |
            validated, immutable document revisions
                       |
             decoder + resolved page mapping
                       |
                PDF worker process
                       |
       original PDF / raw archive / annotated PDF
                       |
          versioned storage + desktop download/view
```

Use a normal Python container or VM for the first hosted worker: the existing Python parser, MuPDF native library and optional PyTorch do not require a serverless rewrite. Run acquisition/export without OCR initially; schedule recognition separately if wanted. The desktop GUI can continue consuming downloaded snapshots. A browser interface is an additional product choice, not a prerequisite for eliminating USB.

Keep account tokens in protected secret storage, acquire/export by UUID and immutable revision/hash, retry with backoff, expose last successful sync and failed exports, and retain older versions. Storage should separate raw documents from rebuildable SQLite indexes; copying active SQLite/WAL files through a file-sync service is not a database replication design. Begin with one-way acquisition; two-way edits introduce a distinct conflict/ownership problem.

## Original-PDF preservation design

Preserve three artifacts by default:

1. **Original PDF**: retain original bytes unchanged, with a checksum.
2. **Annotated PDF**: a separate copy retaining original text/vector content, with highlights, handwriting, drawings and typed notes added at the correct positions.
3. **Raw document revision**: `.rmdoc` or complete raw files, including metadata/content/scenes, for future re-rendering and editable tablet data.

Optionally produce a fourth, flattened visual copy for consistent display. Flattening must make note text visible before discarding annotation structures: baking a sticky-note icon alone loses its underlying text. [PyMuPDF annotation/save/bake APIs](https://pymupdf.readthedocs.io/en/latest/document.html#Document.bake)

Reuse scene decoding, but export in original PDF page coordinates, not the translated viewer canvas. For the currently measured unrotated/untransformed placement, invert the base placement: `pdf_x = (tablet_x - base_left) / PDF_SCALE`, `pdf_y = (tablet_y - base_top) / PDF_SCALE`, and scale widths similarly. This is only the starting transform; crop, rotation, actual device placement, pan/zoom and page mapping require tested handling. PyMuPDF insertion coordinates are relative to unrotated pages. [Page API](https://pymupdf.readthedocs.io/en/latest/page.html)

Represent snapped highlights as native PDF highlights when possible, with color and highlighted text. Draw variable-width ink as vector outlines or a transparent overlay for brush appearance; standard Ink annotations alone cannot represent all pen variation. Add typed notes as text/FreeText or visibly rendered content. Preserve source PDF text, links and structure where feasible; do not rasterize the entire original page as the export strategy. [Annotation recipes](https://pymupdf.readthedocs.io/en/latest/recipes-annotations.html)

Inserted pages and margin/overflow notes need supplementary pages or an explicit enlarged-page edition. A copy preserving original page dimensions cannot place off-page content inside those dimensions without changing its placement. Reordered/duplicate/removed tablet pages require an explicit export order policy and provenance, while the untouched original remains available. Ordinary PDF export does not automatically establish PDF/A conformance.

Write outputs to a new temporary destination, reopen and validate page mapping/count and note coverage, then publish. Guard against overwriting the original or previously preserved revisions. Record document UUID, acquired revision, original hash, exporter version, timestamps and warnings in a manifest.

## Implementation sequence and acceptance criteria

1. **Make acquisition recoverable.** Reject unsafe archives; validate staged documents; retain old versions; add writer locking and explicit failure states. Inject incomplete downloads, move failures and interruptions: the previous readable version must survive.
2. **Resolve pages and enrich annotations.** Model stable IDs, source-PDF mappings, deletion, inserted pages, colors/visibility and device coordinates. Test against real reordered, inserted, landscape and cropped PDFs; fix the confirmed GUI/layout defects.
3. **Ship local preservation export.** Add CLI and GUI export using the same core function. Compare representative results against tablet/official exports; verify searchable base text and that every highlight/note is represented or reported as a failure. Cover colored highlights, multiple pens, hidden layers, typed notes, overflow and duplicate source pages.
4. **Prove cloud transport.** Pair a read-only client with the account; acquire one annotated PDF, compare its raw files with a matching local snapshot, then prove repeated acquisition, changed revisions, expired/revoked credentials and incomplete downloads. No account integration was tested in this review.
5. **Add local cloud sync.** Download into immutable versions, refresh search after publication, automatically regenerate affected PDF outputs, and show freshness/failure status. Do not prune historical revisions based on cloud disappearance.
6. **Host the worker if required.** Move proven acquisition/export jobs to an always-on container/VM, store artifacts durably, and let the desktop fetch them. Verify restart recovery and retrieval while the PC is off before adding a web UI or two-way editing.

## Validation performed

- Existing suite: **128 passed, 11 skipped** (`python -m pytest -q`). Skipped coverage includes opt-in real-data/device checks and Windows ink recognition; this review does not establish fresh parity against the user's actual tablet.
- Ruff: **one F401 finding**, unused `PAPER_SIZES` import at `src/remarksync/mdcompile.py:33`.
- Isolated temporary probes confirmed incomplete-fetch data loss, archive hard-link escape, GUI compilation failure, lost snapped-highlight color, later-page Paper Pro template loss, the 108-pixel frame inconsistency, and stale search after a corrupted page is repaired without metadata changes.
- Static inspection confirmed the missing source-PDF mapping and lack of cloud/export entry points. Documentation establishes the PDF-threading risk; no threading crash was induced.
- Initial sandbox runs could not access the virtual environment's Windows Store interpreter. The same existing environment ran successfully through approved escalation. No dependency installation or environment replacement was needed.

Further external capability details and primary-source links are in [cloud-sync-and-pdf-preservation-sources.md](cloud-sync-and-pdf-preservation-sources.md).
