# Page coordinates follow the tablet: ink y from the page top, text y from the page centre

Earlier versions normalised every notebook page (pulled the ink up to a top
margin and centred it) because the v6 coordinate frame was uncertain. On
2026-09-03 we compared real pages against the reMarkable desktop app: ink is
drawn exactly at its stored position with y measured from the top of the
paper and x from the centre, which is also rmc's convention. Content is
therefore kept where the tablet put it, and the canvas grows around the paper
when ink overflows, on every side and for every document kind. Typed text
blocks are the exception: their stored y is taken as relative to the page
centre, because the two on-device observations we have (rmscene's default
`pos_y=234` lands mid-page; `-780` lands near the top) only fit that reading.
No typed text existed in the backup to verify this against the desktop app,
so it is a single named constant in the page decoder and should be checked
the first time a page with typed text is available.

Do not reintroduce re-centring or margin-pulling in the layout: it was a
workaround, and it misplaced everything relative to the tablet.
