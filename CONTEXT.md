# RemarkSync

A local-first library, viewer, search index and format compiler for reMarkable
tablets, working from preserved copies of the tablet's own document data.

## Language

### The backup

**Backup folder**:
A directory holding a raw copy of the tablet's document store (the xochitl
directory), one set of files per document, usable as a local library.
_Avoid_: mirror, root, dump

**Library**:
A backup folder that has been opened: its document tree, lookup of any
document, and the search index kept beside it.
_Avoid_: workspace, session, catalogue

**Document**:
One item on the tablet with a name, a place in the folder tree, and a list of
pages. Comes in three kinds: a notebook, an annotated PDF, or an annotated
EPUB.
_Avoid_: file, item, entry

**Base document**:
The PDF or EPUB a document was created from, drawn underneath the ink. The
tablet stores a PDF rendering of every EPUB, so both kinds have one.
_Avoid_: source file, attachment

**Archive**:
A single editable tablet document packaged as one `.rmdoc`, acquired from
the tablet/cloud or compiled locally; it can be opened outside a library.
_Avoid_: export, bundle, zip

### Sync and preservation

**Cloud source**:
The account's document data in reMarkable Cloud, populated when the tablet syncs.
_Avoid_: central server (that is our separately hosted service)

**Central server**:
The always-on RemarkSync host that acquires cloud documents and preserves their
raw revisions and annotated PDFs for access by desktop clients.
_Avoid_: reMarkable Cloud, tablet

**Cloud revision**:
One cloud-indexed version of a document, identified by its document identity
and content hash; it is separate from its display name and modification time.
_Avoid_: filename version, latest copy

**Preservation PDF**:
A separate copy of a base PDF with visible highlights, ink and typed notes,
plus the original and raw evidence needed to assess what was preserved.
_Avoid_: original PDF, editable tablet archive

**Rendering**:
One preservation PDF derived from a raw revision; correcting the renderer can
produce another rendering while retaining the same raw revision.
_Avoid_: cloud revision

**Desktop download**:
A verified local copy of the central server's published archives and PDFs,
usable offline and refreshed independently of the tablet.
_Avoid_: two-way sync, upload

**Removed from tablet**:
A preserved document the cloud source no longer lists. It is marked with when
that was first seen and kept in full; the mark only changes how it is shown.
_Avoid_: deleted, pruned, missing (the sync report's word for one pass)

**Whole-library backup**:
A portable copy of retained document revisions, preservation PDFs and the
information needed to verify them and reconstruct the current library.
Account credentials and rebuildable caches are separate from document backup.

**Restore**:
Reconstructing a readable library from verified preserved data into a new
location, retaining the original backup as independent evidence.
_Avoid_: sync, cloud upload

### A page

**Page**:
One drawable surface of a document, holding ink, typed text and highlights, in
page coordinates. Pages of a notebook may be endless (taller than the paper).

**Page coordinates**:
The tablet's own frame for a page: x from the paper's left edge, y from its
top, in tablet pixels. Content keeps these positions everywhere; nothing is
re-centred or pulled to a margin.
_Avoid_: normalised coordinates, raw coordinates, screen units

**Paper**:
The tablet's page rectangle, and not one size: 1404 by 1872 pixels on a
reMarkable 1 or 2, 1620 by 2160 on a Paper Pro. Each page records its own, so
a library that has seen both tablets holds both. Content may extend past it on
any side.
_Avoid_: page bounds, viewport, screen size

**Page frame**:
Where a page lands on a canvas: the canvas size, where the paper sits, what
is drawn behind the ink (a template or the base document's page), and the
divider lines marking paper edges that content overflows.
_Avoid_: layout, geometry, viewport

**Canvas**:
The area a page is drawn into. It starts with the paper and grows downward
and sideways for overflow; the viewer clips content above the paper.

**Underlay**:
The base document's page drawn behind the ink at the tablet's measured physical
scale, centered on the declared paper width with its top at the paper top.
_Avoid_: background image, PDF layer

**Template**:
The ruled, dotted, grid or isometric pattern printed on a notebook page. Only
the first page of an endless canvas shows it.
_Avoid_: background, paper style

**Divider**:
A dashed line on the canvas marking a paper edge that content has overflowed.

**Stroke**:
One pen line: a polyline in page coordinates carrying the width the tablet
computed at every point, its colour, and whether the pen is translucent
(highlighter, shader) or opaque (everything else, the marker included).
_Avoid_: line, path, ink segment

**Ink**:
All of a page's strokes taken together.
_Avoid_: drawing, handwriting layer

**Text block**:
A box of typed text on a page, with a position, a width and paragraphs, each
of a style (plain, bold, heading, bullet, checkbox).
_Avoid_: text box, typed layer, root text

**Highlight**:
A stretch of the base document's own text the user marked on the tablet,
carrying that text and the rectangles it covers.
_Avoid_: snap highlight, glyph range, annotation

**Text metrics**:
The shared facts about typed text: how tall each paragraph style is, where a
text block sits by default, how wide each glyph is, and how the painter sizes
its font. Every number in it was measured from the tablet's own PDF exports
rather than chosen.

**Column width**:
How wide a text block is. A setting rather than a constant: the device offers
740, 936 and 1152 pixels and the block records which it was given. A block is
centred on its page, so the margin follows from the paper.
_Avoid_: text width (the property is called that in the file), line length

**Advance**:
How far the device moves along after setting a glyph — its width for the
purpose of wrapping, in ems of the font size. Measured as the step between two
glyph origins in an export.
_Avoid_: glyph width, character width

**Ligature**:
A pair of letters the font sets as a single glyph, narrower than the two apart.
Indivisible: the device ends a line early rather than break inside one. The two
faces disagree about which pairs are ligatures.

**Kerning**:
The advance depending on which glyph comes next, so that a comma takes one
width before a letter and another before a comma. Real in both faces and not
modelled: an advance is recorded as the step onto an ordinary letter.

### Search

**Search index**:
The full-text index of a library's pages, stored inside the backup folder,
kept up to date one document at a time.
_Avoid_: database, catalogue

**Refresh** (of the index):
Bringing the index up to date: documents changed since they were last indexed
are re-indexed, unchanged ones skipped, vanished ones purged.
_Avoid_: update, sync, incremental build

**Rebuild** (of the index):
A refresh that treats every document as changed.
_Avoid_: reindex, full build

**Extractor**:
A way of turning a page into searchable text. Typed text and highlights are
one extractor, the tablet's own conversion of a page another, handwriting
recognition a third.
_Avoid_: tokenizer, indexer plugin

**Engine** (of handwriting recognition):
The program that reads ink: TrOCR (the default, local, line by line) or
the Windows ink recogniser. Recognitions are cached per engine.
_Avoid_: OCR backend, model (which is TrOCR's weights, not the seam)

**Ink line**:
One written line of a page, as the strokes whose vertical centres cluster
together. What the engine reads; cut by geometry, never by pixels.
_Avoid_: text line (typed text has those), row

**Hit**:
One search result: a document, a page number and a snippet.
_Avoid_: match, result row

### Compiling

**Compile**:
Turning a Markdown file into an archive whose pages hold editable text blocks
the tablet can open. The compiler never wraps or breaks a paragraph: it hands
whole paragraphs over and the device wraps them, which is why the text metrics
have to predict the device's line breaks rather than impose our own.
_Avoid_: convert, export, render

**Send**:
Uploading one archive from this PC to the cloud source so the tablet receives
it at its next sync, either as a new document or in place of the document it
was edited from. Always asked for; never part of a sync.
_Avoid_: sync, push, upload (for the user-facing action)

**Draft**:
A notebook loaded for editing, with the changes asked of it. It becomes an
archive only when saved or sent; the document it came from is never written.
_Avoid_: working copy, edit session

**Target paper**:
The paper a compiled archive is built for. Worth knowing because a taller page
holds more, so it decides where the page breaks fall. Taken from the library
being compiled against — the tablet that wrote it — and the larger where a
library has seen two tablets.
_Avoid_: page size, device size
