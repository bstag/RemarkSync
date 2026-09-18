# RemarkSync compile test

## Every marker, once

### This line should be bold

Plain paragraph. This sentence is deliberately long so that the tablet has to wrap it itself rather than receiving it pre-broken, because the compiler never wraps and the device is supposed to do that work when it opens the page.

- bullet at the top level
  - nested bullet, indented by two spaces
1. numbered item at the top level
   1. nested numbered item, indented by three spaces
- [ ] a checkbox nobody has ticked
- [x] a checkbox somebody has ticked

## Things worth checking on the device

Tap into this text. It should be editable, with the caret landing between characters rather than the page behaving like an image.

The heading above should be a heading, not bold text at heading size. The subheading and the bold line should look different from each other, because they share a style code and differ only in fields rmscene skips.

## Filler, to force a second page

Paragraph one of the filler. Each of these is a separate paragraph so the compiler has to decide where the page ends, and a paragraph is never supposed to be split across a page boundary.

Paragraph two of the filler, with enough words in it to occupy more than a single line once the tablet has wrapped it to the width of the text block.

Paragraph three of the filler. If pagination is right, this document should run to two or three pages and no paragraph should be cut in half at a page break.

Paragraph four of the filler, again long enough to wrap onto a second line so that the line accounting is exercised rather than trivially satisfied.

Paragraph five of the filler. The last page should not be padded out or truncated, and the text should start at the same place on every page.

Paragraph six of the filler, which exists mainly so that the second page has something on it after the break lands.

Paragraph seven of the filler. This is the end of the document, and on the tablet it should be followed by empty space rather than by anything left over.
