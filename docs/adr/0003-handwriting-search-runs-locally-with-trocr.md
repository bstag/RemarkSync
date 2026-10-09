# 0003. Handwriting search runs locally, with TrOCR

Date: 2026-09-04. Status: accepted.

## Context

Search should find words written by hand, not only typed text. The
tablet's own recognition (MyScript) is the best reading of this ink, but
it only reaches the backup when a page is converted on the device, and
that is a manual act per page. For the rest of the library an engine has
to read the strokes from the backup. Options weighed, all at no cost:

* **MyScript cloud**, the same engine the tablet uses: 2,000 requests a
  month free, then paid; sends the ink off the machine, which the data
  local handwriting-processing policy rules out as a default.
* **Windows ink recogniser** (WinRT `InkRecognizerContainer`): offline,
  roughly two words in three on cursive, but it fails outright on a
  3,000-stroke page, stalls on some shapes, and a stall wedges the engine
  for every process on the machine for about six minutes. Also it must be
  driven from a helper process because it deadlocks with Qt at exit.
* **TrOCR** (`microsoft/trocr-large-handwritten`, MIT-licensed model
  weights, PyTorch): offline, line-based, needs the page cut into written
  lines first. Measured on a 532-word journal page against the tablet's
  MyScript conversion of the same ink: 80% of the words at the top
  reading, 83% counting runner-up beams, 0.1 s per line on an RTX
  4060 Ti (about ten seconds per dense page; several times slower on a CPU).
  Words it misses are mostly names and lines struck through.

## Decision

TrOCR is the handwriting engine. The page is cut into lines by
geometry from the strokes we already hold (`inklines.py`: the vertical
centres of strokes cluster tightly per written line, so a smoothed
histogram of centres has one peak per line and cuts fall in the
valleys; extents would overlap because of ascenders and descenders).
Each line is rendered black on white at 128 px and read in batches; the
result is cached by the ink's hash under the engine's name, so a page is
read once and a different engine never reuses a stale reading. Runner-up
readings, when enabled, are appended as an "alternatives" line so a
search matches them too.

**Amended 2026-09-17:** runner-up readings shipped switched off, so the 83%
above describes a path no user can reach. `TrOCRModel` defaults to
`beams=1, alternatives=0` and `trocr_model()` takes no arguments, so nothing
in the CLI or the window can turn beams on; the alternatives code is
exercised only by tests. Recall as shipped is the 80% single-beam figure.
Enabling beams costs generation time per line and has not been measured
end-to-end, so it stays off until someone wires a flag through and re-times
it.

The engine is a seam, not a special case: `recognize.handwriting_extractor`
picks TrOCR when PyTorch and transformers are importable, otherwise the
Windows recogniser, and `--engine` names one explicitly. Both are
optional extras in `pyproject.toml`; the base install has no
machine-learning dependencies.

## Consequences

* Handwriting recognition is opt-in (`index --handwriting`) and needs a
  1.3 GB model download once; on a GPU a whole library indexes in a few
  hours the first time and only changed pages afterwards.
* Recall is 80%, not 100%: a search can miss a handwritten word. The
  tablet's own conversion of a page ("convert to text, add as new page")
  is indexed for free and is the fix for pages that matter. A conversion
  shared from the device lands beside the document without changing its
  metadata, and the index notices that on its own (`search.py` keys a
  document on its conversions as well as its timestamp).
* The line cutter assumes roughly horizontal writing. Drawings, tables and
  sideways notes produce lines that read as noise; struck-through text is
  read as whatever the strokes look like.
* MyScript cloud remains possible behind the same seam if someone wants
  it, for the free monthly quota, but would need its own opt-in because
  it sends ink off the machine.

## Scope clarified 2026-10-03

The decision keeps handwriting recognition local; it does not prohibit the
user's separately selected reMarkable Cloud and private server acquisition
workflow. Hosted sync/export does not run OCR, and no external handwriting API
is called. The quoted service limits, prices and September measurements above
are historical decision context, not current service offers or a new benchmark.
