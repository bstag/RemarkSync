# Page coordinates follow the tablet: y from the page top for ink and text alike

Earlier versions normalised every notebook page (pulled the ink up to a top
margin and centred it) because the v6 coordinate frame was uncertain. On
2026-09-03 we compared real pages against the reMarkable desktop app: ink is
drawn exactly at its stored position with y measured from the top of the
paper and x from the centre, which is also rmc's convention. Content is
therefore kept where the tablet put it, and the canvas grows around the paper
when ink overflows, on every side and for every document kind.

Typed text was first assumed to be centre-relative in y, on the strength of
two second-hand observations. On 2026-09-04 two pages typed on the device
were exported to PDF by the desktop app (vector exports at 226 dpi) and
fitted against our decode of the same files: text y is top-relative like
ink. The model that reproduces both pages (see `textmetrics.py`): the first
line's baseline is 61 px below the block's pos_y for every style; the
baseline-to-baseline distance between paragraphs is linear in the two font
sizes (body 69.3 px, headings 163 px, mixed transitions within 0.3 px except
subheading-to-body at 7 px); lines inside a wrapped paragraph are closer
(the font's own line height); text starts 15 px right of pos_x. An ink
layer's anchor is a character id, and the layer is placed relative to the
baseline of that character's line; a paragraph's start id is the newline
that ends the previous paragraph, so ink anchored there rides that line.
rmscene drops an empty first paragraph when the text begins with a newline
(the file's own line count is one higher), and that line must be restored
or everything below it lands one line high. rmc's -88 top offset and 70 px
lines are wrong for this firmware.

Do not reintroduce re-centring or margin-pulling in the layout, and do not
shift text y by half a page: both were workarounds that misplaced content.

## Amended 2026-09-17: the paper is per page, and the canvas grows three ways

The decision stands — content keeps the tablet's own coordinates and nothing
is re-centred — but two things stated around it were wrong, and both were
caught by comparing against the tablet's own renderings rather than against
our own reasoning.

**The paper is not one size.** It was a module constant, 1404x1872. A page
declares its own size in its `SceneInfo`, and a Paper Pro sheet is 1620x2160;
one notebook in the reference backup holds pages of both. Six of twenty pages
sampled were laid out on the wrong sheet, and because v6 stores x relative to
the paper's centre they were also shifted by 702 where they needed 810, so
every stroke sat 108 px left of where the tablet had it. Three independent
sources agree on the size: `SceneInfo.paper_size`, `.content`'s
`customZoomPageWidth`/`customZoomPageHeight`, and the page box of the PDF the
tablet exports.

**The canvas does not grow upward.** It was growing in all four directions.
The tablet scrolls down and sideways without limit but clips at the paper top:
a page whose ink reaches y = -83.7 still exports at exactly paper height. It
also does not grow at all while content stays inside the paper — two pages
ending at y = 1871.5, just inside the 1872 sheet, export paper height where we
were adding a margin and growing.

**And the margin was 24 px, not 47.** Fitted against the exported page box on
the pages that overflow and carry no typed text: they need 47.8, 45.5 and
46.6 px. See `GROWTH_MARGIN`.

Measured against the desktop app's own page renders, these two changes moved
v5 from 1.089 to 1.031 of the device's ink mass and its placement from 26.8 to
5.4 tablet pixels, and v6 from 1.040 to 1.030 and 7.9 to 5.4 px. The rules
themselves are asserted against the exported page box in
`tests/real/test_device_pdf_parity.py`.

Still open: horizontal growth looks quantised — two pages needing 1797 and
1779 px both export at exactly 1873 — and `_extent` underestimates a typed
text block, because `TextBlock.height` is a default rather than the height of
the block's laid-out lines.
