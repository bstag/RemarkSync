"""Measure the glyph advances the tablet actually uses, from its own line breaks.

``textmetrics`` scales a character's width as ``advance * font size``, with the
advances measured from the device's exports. Give it a paragraph of one
character repeated and the width of the lines it draws pins that character's
advance: a line holding ``n`` of them is ``n * advance * size`` px wide.

Every letter and digit was measured this way on 2026-09-18. What was left
afterwards is punctuation -- the serif face had none of it at all, so a heading
carrying a comma or an apostrophe wrapped on the fallback average.

Punctuation needed more care than letters did, and taught us two things.

**The faces kern.** A mark's advance depends on what follows it: the serif sets
``,`` 0.237 em before another comma and 0.248 before a letter, and ``x`` 0.496
before ``!`` but 0.453 before ``-``. So there is no single advance to read off a
run of one mark, and nobody types ``,,,,`` anyway. Each character is therefore
set twice, alone and paired with a letter, and it is the step **onto the letter**
that is recorded, that being the step ordinary text takes. The difference between
the two is reported as the kerning it is; ``textmetrics`` does not model it.

**The step is the measurement.** Two glyph origins apart is the advance, plainly.
The older reading -- a line's width shared out over its glyphs -- has to be told
the partner's advance before it can subtract it, and a stale value for ``x``
biased every paired reading by 0.013 em before this was noticed.

The fixture cannot go through Markdown, which would strip ``*``, ``_`` and
backticks as emphasis and read a leading ``#`` or ``-`` as a block marker.
:mod:`tests.real.fixture` hands the compiler styled paragraphs instead.

    python -m tests.real.advances build     # write the fixture and compile it
    python -m tests.real.advances upload    # POST it to the tablet
    python -m tests.real.advances measure   # derive advances from the export

``build`` and ``measure`` are local; ``upload`` writes one notebook to the
tablet, which can be deleted afterwards.
"""

from __future__ import annotations

import string
import sys
from statistics import median

from remarksync.textmetrics import (ADVANCE_SANS, ADVANCE_SERIF, DEFAULT_ADVANCE_SANS,
                                    DEFAULT_ADVANCE_SERIF, LIGATURES_SANS, LIGATURES_SERIF, style)

from .exported import drawn_paragraphs
from .fixture import compile_runs, style_for
from .paths import work_dir
from .tablet import document_id, export_pdf, upload as post_archive

WORK = work_dir("REMARKSYNC_ADVANCES", "rs-advances")
TITLE = "RemarkSync advance scale"
ARCHIVE = WORK / "advances.rmdoc"

ALNUM = string.ascii_letters + string.digits
# The marks a note actually carries: ASCII, then the typographic forms a word
# processor would have produced.
PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~" + "–—‘’“”…"
CANDIDATES = ALNUM + PUNCTUATION
# Long enough that even the widest plausible glyph wraps at least twice, so a
# full line's count is unambiguous, and short enough to keep the document small.
RUN = 220
# One control per face: a character already measured, so the method can be
# checked against a known value before any new number is believed.
CONTROL = "g"
# The letter each character is also set against, so its advance can be read
# stepping onto a letter rather than onto its own kind -- which is what ordinary
# text does, and what the kerning makes different.
PARTNER = "x"
# The widest glyph either face has (serif 'W'). A reading past it is a failed
# measurement, not a wide character.
WIDEST = 1.02


def _table(serif: bool) -> dict[str, float]:
    return ADVANCE_SERIF if serif else ADVANCE_SANS


def _missing(serif: bool) -> list[str]:
    table = _table(serif)
    return [c for c in CANDIDATES if c not in table]


def _units(char: str, serif: bool) -> list[str]:
    """The runs to set for a character: the character alone, and the character
    paired with a known letter. A character whose doubled form the font ligates
    can only be measured paired -- a run of it is not a run of it."""
    ligatures = LIGATURES_SERIF if serif else LIGATURES_SANS
    units = [] if char + char in ligatures else [char]
    return units + [char + PARTNER]


def build() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    plan: list[tuple[str, str, str]] = []
    paragraphs: list[tuple[str, str]] = []
    for serif in (True, False):
        style_name = style_for(serif)
        for char in [CONTROL] + _missing(serif):
            for unit in ([CONTROL] if char == CONTROL else _units(char, serif)):
                paragraphs.append((style_name, unit * (RUN // len(unit))))
                plan.append((style_name, char, unit))
    (WORK / "plan.txt").write_text(
        "\n".join(f"{s}\t{c}\t{u}" for s, c, u in plan), encoding="utf-8")
    out = compile_runs(TITLE, ARCHIVE, paragraphs)
    print(f"{len(plan)} runs over {len({c for _, c, _ in plan})} characters in two faces")
    print(f"wrote {out.name} ({out.stat().st_size} bytes)")


def upload() -> None:
    if not ARCHIVE.exists():
        print("no archive; run 'build' first")
        return
    print(post_archive(ARCHIVE))


def _gaps(group: list[list[tuple[str, float, float]]], size: float) -> dict[str, float]:
    """How far the device moved on from each glyph to the next, in em, keyed by
    the pair the step spans and taken as the median over the run.

    This is the advance itself, read where it is written rather than inferred:
    the distance between two glyph origins is what the device advanced by. The
    older reading -- a line's width shared out over the glyphs on it -- gives
    the same answer only when every glyph on the line is the same width, and it
    needs the partner's advance to be known in advance, which was how a stale
    value for ``x`` quietly biased every paired measurement by 0.013.
    """
    steps: dict[str, list[float]] = {}
    for line in group:
        for (char, x0, _x1), (following, next_x0, _) in zip(line, line[1:]):
            steps.setdefault(char + following, []).append((next_x0 - x0) / size)
    return {pair: median(values) for pair, values in steps.items() if len(values) >= 3}


def measure() -> None:
    """Derive each character's advance from the tablet's own rendering of it."""
    uuid = document_id(TITLE)
    if uuid is None:
        print(f"the tablet does not list {TITLE!r}; run 'upload' first")
        return
    pdf_path = export_pdf(uuid, WORK)
    print(f"the tablet's export: {pdf_path.stat().st_size} bytes")

    plan_path = WORK / "plan.txt"
    if not plan_path.exists():
        print("no plan.txt; run 'build' again so the run order is known")
        return
    plan = [tuple(line.split("\t")) for line in plan_path.read_text(encoding="utf-8").splitlines()]

    runs = drawn_paragraphs(pdf_path)
    if len(runs) != len(plan):
        print(f"!! {len(runs)} runs in the export but {len(plan)} in the plan")
        return

    # every step the device took, keyed by the character it stepped off and the
    # run it was seen in
    readings: dict[tuple[str, str], dict[str, float]] = {}
    for (style_name, char, unit), group in zip(plan, runs):
        gaps = _gaps(group, style(style_name).size)
        seen = readings.setdefault((style_name, char), {})
        if char + char in gaps:
            seen["alone"] = gaps[char + char]
        if char + PARTNER in gaps:
            seen["paired"] = gaps[char + PARTNER]

    results: dict[bool, dict[str, float]] = {True: {}, False: {}}
    for serif in (True, False):
        style_name = style_for(serif)
        s = style(style_name)
        table = _table(serif)
        default = DEFAULT_ADVANCE_SERIF if serif else DEFAULT_ADVANCE_SANS
        print(f"\n-- {'serif' if serif else 'sans'} ({style_name}) at size {s.size} --")
        print(f"{'char':>6} {'alone':>7} {'paired':>7} {'take':>7} {'table':>7}  note")
        for char in [CONTROL] + _missing(serif):
            seen = readings.get((style_name, char), {})
            alone = seen.get("alone")
            paired = seen.get("paired")
            # the step onto a letter is the one ordinary text takes: nobody
            # types ',,,,', and the face kerns, so a mark's advance among its
            # own kind is not the advance it has in a sentence
            take = paired if paired is not None else alone
            notes = []
            if take is None:
                print(f"{char!r:>6} {'':>7} {'':>7} {'':>7} {'':>7}  not in the export")
                continue
            if paired is None:
                notes.append("no step onto the partner: taking the run of its own")
            elif alone is not None and abs(alone - paired) > 0.01:
                notes.append(f"kerns {alone - paired:+.3f} against its own kind")
            # nothing in either face is wider than 'W', so a reading past it is
            # the measurement failing rather than a wide glyph: the serif grave
            # composes with the letter after it and reads 1.079 that way
            if take > WIDEST:
                print(f"{char!r:>6} {f'{alone:.3f}' if alone is not None else '':>7} "
                      f"{f'{paired:.3f}' if paired is not None else '':>7} {take:7.3f} "
                      f"{'':>7}  IMPLAUSIBLE (past {WIDEST}), left out")
                continue
            known = table.get(char)
            if known is None:
                results[serif][char] = round(take, 3)
                notes.append(f"was the {default} fallback")
            else:
                off = abs(take - known) / known
                notes.append(f"control: {'agrees' if off < 0.02 else 'DISAGREES'} "
                             f"({off * 100:.1f}% from {known})")
            print(f"{char!r:>6} {f'{alone:.3f}' if alone is not None else '':>7} "
                  f"{f'{paired:.3f}' if paired is not None else '':>7} {take:7.3f} "
                  f"{known if known is not None else '':>7}  {'; '.join(notes)}")

    for serif in (True, False):
        if results[serif]:
            print(f"\nready to paste into ADVANCE_{'SERIF' if serif else 'SANS'}:")
            print("    " + ", ".join(f"{c!r}: {v}" for c, v in sorted(results[serif].items())))


MODES = ("build", "upload", "measure")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
