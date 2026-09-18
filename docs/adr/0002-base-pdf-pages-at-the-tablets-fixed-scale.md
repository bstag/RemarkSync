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

## Confirmed 2026-09-17, from a second source

The scale was originally fitted from the tablet's own snap-highlight
rectangles on three PDFs. It now has independent confirmation: the tablet
exports a document as a PDF over its USB web interface, and for a page that
does not overflow, that PDF's page box is the paper.

A 1404x1872 page exports at 445x594 pt, which is 3.1551 px/pt by width and
3.1515 by height, bracketing `PDF_SCALE = 3.1527`; 1404 / 3.1527 = 445.32,
which rounds to the 445 the device writes. A Paper Pro page exports at
514x685 pt, and 1620 / 3.1527 = 513.8.

Two other things that export shows, both worth knowing:

* the ink arrives as vector paths, so it can be rasterised at 1:1 with tablet
  pixels — 1404 px wide, against the 384 px of the desktop app's thumbnails;
* 101 of 119 paths on the page checked are *filled*, with the only stroke
  width present being 0.159 pt (about half a tablet pixel). The device draws
  ink as filled outlines, which is what `viewer._stroke_outline` does. That
  choice was made on reasoning and is now confirmed.
