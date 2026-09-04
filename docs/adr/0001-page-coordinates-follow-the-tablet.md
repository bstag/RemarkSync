# Page coordinates follow the tablet: y from the page top for ink and text alike

Earlier versions normalised every notebook page (pulled the ink up to a top
margin and centred it) because the v6 coordinate frame was uncertain. On
2026-09-03 we compared real pages against the reMarkable desktop app: ink is
drawn exactly at its stored position with y measured from the top of the
paper and x from the centre, which is also rmc's convention. Content is
therefore kept where the tablet put it, and the canvas grows around the paper
when ink overflows, on every side and for every document kind.

Typed text was first assumed to be centre-relative in y, on the strength of
two second-hand observations. On 2026-09-04 a page typed on the device was
exported to PDF by the desktop app (a vector export at 226 dpi) and fitted
against our decode of the same file: text y is top-relative like ink; the
first line box starts 60 px below the block's pos_y (rmc's -88 is wrong for
this firmware); plain lines are 69.3 px apart (rmc rounds to 70); glyph tops
sit 39 px below their line box; text starts 15 px right of pos_x. Ink layers
anchored to text lines are placed with the same numbers, so a page with
typed text and ink reflows exactly as on the device.

Do not reintroduce re-centring or margin-pulling in the layout, and do not
shift text y by half a page: both were workarounds that misplaced content.
