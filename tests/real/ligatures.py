"""Which letter pairs the device fonts set as one glyph, and how wide it is.

The compiler hands whole paragraphs to the tablet and the tablet breaks them,
so our wrapping has to predict its line breaks. Two of the advance measurements
ran into the same obstacle from opposite sides: a run of ``f`` could not be
measured at all because ``ff`` came back as a single glyph, and the one line in
the three-width worksheet we still broke differently ended in ``f`` where the
device moved the whole ``fj`` down to the next line.

So a ligature is not a display detail. It changes the width of the text *and*
it is indivisible -- the device would rather end a line early than split one.
Both facts have to reach :mod:`remarksync.textmetrics`, and both can be read off
an export: give the device a paragraph of one pair repeated, and

* the glyph stream says whether the pair ligated, because a ligature arrives as
  one glyph where the pair would be two (with a Unicode codepoint of its own if
  it has one -- fi, fl and ff do -- and as an unmapped control character if not,
  which is how fh and fj turn up);
* a full line's width divided by the ligatures on it gives the advance, the same
  way :mod:`tests.real.advances` measured single characters;
* and an odd character count on any line would mean the pair *can* be split.

    python -m tests.real.ligatures build     # write the fixture and compile it
    python -m tests.real.ligatures upload    # POST it to the tablet
    python -m tests.real.ligatures measure   # read the ligatures off the export

``build`` and ``measure`` are local; ``upload`` writes one notebook to the
tablet, which can be deleted afterwards.
"""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path

from remarksync.textmetrics import ADVANCE_SANS, ADVANCE_SERIF, style

from .exported import drawn_paragraphs
from .fixture import compile_runs, style_for
from .tablet import document_id, export_pdf, upload as post_archive

WORK = Path(os.environ.get("REMARKSYNC_LIGATURES", str(Path(os.environ["TEMP"]) / "rs-ligatures")))
TITLE = "RemarkSync ligature scale"
ARCHIVE = WORK / "ligatures.rmdoc"

# Every pair worth asking about. The f-ligatures are the classic set and the
# only ones either export has shown, so the candidates are f followed by each
# lowercase letter; the device answers for all of them at once and the ones that
# do not ligate cost nothing but a line of the fixture.
CANDIDATES = [f"f{c}" for c in string.ascii_lowercase]
# Long enough to fill more than one line in the widest column, so at least one
# line is full and its width is a measurement rather than a remainder.
RUN = 220


def build() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    plan: list[tuple[str, str]] = []
    paragraphs: list[tuple[str, str]] = []
    for serif in (True, False):
        style_name = style_for(serif)
        for pair in CANDIDATES:
            paragraphs.append((style_name, pair * (RUN // len(pair))))
            plan.append((style_name, pair))
    out = compile_runs(TITLE, ARCHIVE, paragraphs)
    (WORK / "plan.txt").write_text("\n".join(f"{s}\t{p}" for s, p in plan), encoding="utf-8")
    print(f"{len(plan)} runs over {len(CANDIDATES)} pairs in two faces")
    print(f"wrote {out.name} ({out.stat().st_size} bytes)")


def upload() -> None:
    if not ARCHIVE.exists():
        print("no archive; run 'build' first")
        return
    print(post_archive(ARCHIVE))


def measure() -> None:
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

    results: dict[bool, dict[str, float]] = {True: {}, False: {}}
    for serif in (True, False):
        style_name = style_for(serif)
        s = style(style_name)
        table = ADVANCE_SERIF if serif else ADVANCE_SANS
        print(f"\n-- {'serif' if serif else 'sans'} ({style_name}) at size {s.size} --")
        print(f"{'pair':>6} {'glyphs':>7} {'advance':>8} {'apart':>7}  note")
        for (plan_style, pair), group in zip(plan, runs):
            if plan_style != style_name:
                continue
            # a glyph whose character is not one of the pair's letters is the
            # ligature: it has either a codepoint of its own (fi, fl, ff) or
            # none at all, in which case the export carries it unmapped
            def ligatures(line):
                return [g for g in line if g[0] not in pair]

            full = max(group, key=len)
            apart = round(sum(table[c] for c in pair), 3) if all(c in table for c in pair) else None
            if not ligatures(full):
                print(f"{pair:>6} {len(full):7} {'':>8} {apart if apart is not None else '':>7}  "
                      f"set as two letters")
                continue
            # a full line of a ligated run is ligatures and nothing else, so its
            # width over their count is the advance
            width = max(g[2] for g in full) - min(g[1] for g in full)
            advance = round(width / (len(full) * s.size), 3)
            results[serif][pair] = advance
            note = "ligature" if apart is None else f"ligature, {advance - apart:+.3f} against the letters"
            # if the device were willing to split one, some line would have to
            # end in a bare 'f' -- a glyph that is a letter of the pair
            if any(len(ligatures(line)) != len(line) for line in group[:-1]):
                note += " -- BUT A LINE SPLIT IT"
            print(f"{pair:>6} {len(full):7} {advance:8.3f} "
                  f"{apart if apart is not None else '':>7}  {note}")

    for serif in (True, False):
        if results[serif]:
            print(f"\nready to paste into LIGATURES_{'SERIF' if serif else 'SANS'}:")
            print("    " + ", ".join(f'"{p}": {v}' for p, v in sorted(results[serif].items())))


MODES = ("build", "upload", "measure")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
