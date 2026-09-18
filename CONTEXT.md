# RemarkSync

A local-first library, viewer, search index and format compiler for reMarkable
tablets, working entirely from a raw backup of the tablet's own files.

## Language

### The backup

**Backup folder**:
A directory holding a raw copy of the tablet's document store (the xochitl
directory), one set of files per document. The only thing RemarkSync reads.
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
A single document exported from the tablet as one `.rmdoc` file. Opened on its
own, outside any library.
_Avoid_: export, bundle, zip

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
The area a page is drawn into. Exactly the paper unless content overflows,
in which case it grows to hold everything.

**Underlay**:
The base document's page drawn behind the ink, fitted inside the paper at its
own aspect ratio and centred, the way the tablet shows it.
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

**Target paper**:
The paper a compiled archive is built for. Worth knowing because a taller page
holds more, so it decides where the page breaks fall. Taken from the library
being compiled against — the tablet that wrote it — and the larger where a
library has seen two tablets.
_Avoid_: page size, device size
