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
