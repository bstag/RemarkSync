# Base PDF pages are placed at the tablet's fixed scale, not fitted to the screen

The obvious way to draw a base PDF under the ink is to fit the page into
the 1404 x 1872 screen. That is not what the tablet does, and doing it put
highlights and ink about a line off on the NIV bible and further off on
US Letter documents. On 2026-09-03 we fitted the tablet's own snap-highlight
rectangles (298 of them, three PDFs, residual about 1 px) against the PDF's
text positions: the tablet draws every PDF at 3.1527 tablet pixels per PDF
point, centred horizontally on the screen width, top edge at y = 0,
regardless of page size. That is physical size on a 157 mm wide screen: a
3:4 page comes out 1362 x 1816, a US Letter page 1930 x 2497 and wider than
the screen. Ink and highlights are stored in that same frame, so the layout
makes the placed base page the paper, draws the underlay exactly there, and
lets the canvas grow around it. Do not "fix" this back to a fit-to-screen:
the tablet's zoom mode only changes what the screen shows, not where
annotations are stored.
