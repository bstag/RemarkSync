"""Measure the glyph advances the tablet actually uses, from its own line breaks.

``textmetrics`` scales a character's width as ``advance * font size``, with the
advances measured from the device's exports. Only 23 of the 62 letters and digits
were ever measured; the rest share one average, so a break point is a character
or two out on text made of them -- ``f`` and ``j`` are narrower than the average
and we break too early, ``A`` and ``M`` are wider and we break too late.

The device will measure the rest for us. Give it a paragraph of one character
repeated, and where it breaks the line pins that character's advance: if a full
line holds ``n`` of them in a column ``limit`` px wide, then

    limit / ((n + 1) * size)  <  advance  <=  limit / (n * size)

One paragraph per character, compiled and uploaded, and the tablet's PDF export
gives every bound at once.

    python -m tests.real.advances build     # write the fixture and compile it
    python -m tests.real.advances upload    # POST it to the tablet
    python -m tests.real.advances measure   # derive advances from the export

``build`` and ``measure`` are local; ``upload`` writes one notebook to the
tablet, which can be deleted afterwards.
"""

from __future__ import annotations

import os
import string
import sys
import urllib.request
from pathlib import Path

from remarksync.textmetrics import (ADVANCE_SANS, ADVANCE_SERIF, DEFAULT_ADVANCE_SANS,
                                    DEFAULT_ADVANCE_SERIF, STYLES, style)

WORK = Path(os.environ.get("REMARKSYNC_ADVANCES", str(Path(os.environ["TEMP"]) / "rs-advances")))
HOST = os.environ.get("REMARKSYNC_TABLET", "10.11.99.1")
TITLE = "RemarkSync advance scale"
ARCHIVE = WORK / "advances.rmdoc"
SOURCE = WORK / "advances.md"

ALNUM = string.ascii_letters + string.digits
# Long enough that even the widest plausible glyph wraps at least twice, so a
# full line's count is unambiguous, and short enough to keep the document small.
RUN = 220
# One control per table: a character already measured, so the method can be
# checked against a known value before any new number is believed.
CONTROLS = {"serif": "g", "sans": "g"}
# 'f' cannot be measured from a run of its own: the device sets 'ff' as a single
# ligature glyph, so the run is neither pure 'f' nor 'f'-width. Pairing it with a
# character that cannot ligature after 'f', whose advance is already measured,
# gives the same answer -- the pair's width minus the known one.
PARTNER = "x"
UNITS = {"f": "f" + PARTNER}


def unit_for(char: str) -> str:
    return UNITS.get(char, char)


def _missing(serif: bool) -> list[str]:
    table = ADVANCE_SERIF if serif else ADVANCE_SANS
    return [c for c in ALNUM if c not in table]


# The style used to measure each face: no indent, so the column is the whole
# text width, and a Markdown marker the compiler already emits. HEADING is the
# serif face and PLAIN the sans one.
FACE_STYLE = {True: ("HEADING", "# "), False: ("PLAIN", "")}


def _style_for(serif: bool) -> str:
    name = FACE_STYLE[serif][0]
    if STYLES[name].serif is not serif or STYLES[name].indent:
        raise SystemExit(f"{name} is no longer the plain {'serif' if serif else 'sans'} style")
    return name


def build() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    lines = []
    plan = []
    for serif in (True, False):
        style_name = _style_for(serif)
        marker = FACE_STYLE[serif][1]
        chars = [CONTROLS["serif" if serif else "sans"]] + _missing(serif)
        for char in chars:
            unit = unit_for(char)
            repeats = max(1, RUN // len(unit))
            lines.append(f"{marker}{unit * repeats}")
            lines.append("")
            plan.append((style_name, char))
    SOURCE.write_text("\n".join(lines), encoding="utf-8")

    from remarksync.mdcompile import markdown_to_rmdoc

    out = markdown_to_rmdoc(SOURCE.read_text(encoding="utf-8"), TITLE, ARCHIVE)
    (WORK / "plan.txt").write_text("\n".join(f"{s}\t{c}" for s, c in plan), encoding="utf-8")
    print(f"{len(plan)} runs ({sum(1 for s, _ in plan if s == plan[0][0])} in the first style)")
    print(f"wrote {SOURCE.name} and {out.name} ({out.stat().st_size} bytes)")
    print("styles used:", sorted({s for s, _ in plan}))


def upload() -> None:
    from .compile_roundtrip import ARCHIVE as _unused  # noqa: F401  (keeps the import graph honest)
    import mimetypes
    import uuid as uuidlib

    if not ARCHIVE.exists():
        print("no archive; run 'build' first")
        return
    boundary = f"----remarksync{uuidlib.uuid4().hex}"
    ctype = mimetypes.guess_type(ARCHIVE.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{ARCHIVE.name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        ARCHIVE.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"http://{HOST}/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Origin": f"http://{HOST}", "Referer": f"http://{HOST}/"})
    with urllib.request.urlopen(request, timeout=300) as response:
        print(f"HTTP {response.status}: {response.read(200).decode('utf-8', 'replace')}")


def _uuid_from_tablet() -> str | None:
    import json

    request = urllib.request.Request(f"http://{HOST}/documents/", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        for entry in json.loads(response.read()):
            if entry.get("VisibleName") == TITLE:
                return entry.get("ID")
    return None


def _runs_from_export(pdf_path: Path) -> list[tuple[str, int, float]]:
    """Every line of the export that is one unit repeated, in document order,
    as (character, characters on the line, the line's width in tablet px).

    The width is what matters: it is measured, where a bound inferred from the
    column edge depends on knowing exactly where the column ends.
    """
    import pymupdf

    from remarksync.layout import PDF_SCALE

    runs: list[tuple[str, int, float]] = []
    with pymupdf.open(pdf_path) as pdf:
        for index in range(pdf.page_count):
            rows: dict[float, list[tuple[float, float, str]]] = {}
            for x0, _y0, x1, y1, word, *_ in pdf.load_page(index).get_text("words"):
                rows.setdefault(round(y1, 1), []).append((x0, x1, word))
            for _, boxes in sorted(rows.items()):
                boxes.sort()
                line = "".join(word for _, _, word in boxes)
                if not line:
                    continue
                width = (max(x1 for _, x1, _ in boxes) - min(x0 for x0, _, _ in boxes)) * PDF_SCALE
                char = None
                for candidate, unit in [(line[:1], line[:1])] + list(UNITS.items()):
                    reps, extra = divmod(len(line), len(unit))
                    if reps and not extra and line == unit * reps:
                        char = candidate
                        break
                if char is None:
                    continue
                # keep the widest line of the run: that is a full one, where the
                # last line of a paragraph is a remainder
                if runs and runs[-1][0] == char:
                    if len(line) > runs[-1][1]:
                        runs[-1] = (char, len(line), width)
                else:
                    runs.append((char, len(line), width))
    return runs


def measure() -> None:
    """Derive each character's advance from the tablet's own rendering of it."""
    uuid = _uuid_from_tablet()
    if uuid is None:
        print(f"the tablet does not list {TITLE!r}; run 'upload' first")
        return
    WORK.mkdir(parents=True, exist_ok=True)
    pdf_path = WORK / f"{uuid}.pdf"
    with urllib.request.urlopen(f"http://{HOST}/download/{uuid}/placeholder", timeout=300) as response:
        pdf_path.write_bytes(response.read())
    print(f"the tablet's export: {pdf_path.stat().st_size} bytes")

    plan_path = WORK / "plan.txt"
    if not plan_path.exists():
        print("no plan.txt; run 'build' again so the run order is known")
        return
    plan = [tuple(line.split("\t")) for line in plan_path.read_text(encoding="utf-8").splitlines()]

    runs = _runs_from_export(pdf_path)
    if len(runs) != len(plan):
        print(f"!! {len(runs)} runs in the export but {len(plan)} in the plan")
        for i in range(min(len(runs), len(plan))):
            if runs[i][0] != plan[i][1]:
                print(f"   first divergence at {i}: export {runs[i][0]!r}, plan {plan[i][1]!r}")
                break
        return

    # the same character is measured once per face, so it has to be keyed by both
    measured = {(style_name, char): (n, width)
                for (style_name, char), (_c, n, width) in zip(plan, runs)}

    print(f"\n{len(measured)} runs measured from {pdf_path.name}")
    results: dict[bool, dict[str, float]] = {True: {}, False: {}}
    for serif in (True, False):
        style_name = _style_for(serif)
        s = style(style_name)
        table = ADVANCE_SERIF if serif else ADVANCE_SANS
        default = DEFAULT_ADVANCE_SERIF if serif else DEFAULT_ADVANCE_SANS
        print(f"\n-- {'serif' if serif else 'sans'} ({style_name}) at size {s.size} --")
        print(f"{'char':>5} {'chars':>6} {'width px':>9} {'advance':>8} {'table':>7}  note")
        for char in [CONTROLS["serif" if serif else "sans"]] + _missing(serif):
            entry = measured.get((style_name, char))
            if entry is None:
                continue
            n, width = entry
            unit = unit_for(char)
            units = n // len(unit)
            others = sum(table.get(c, default) for c in unit[1:])
            advance = round(width / (units * s.size) - others, 3)
            known = table.get(char)
            if known is None:
                results[serif][char] = advance
                note = f"was the {default} fallback"
            else:
                drift = abs(advance - known) / known
                note = (f"control: {'agrees' if drift < 0.02 else 'DISAGREES'} "
                        f"({drift * 100:.1f}% from {known})")
            print(f"{char!r:>5} {n:6} {width:9.1f} {advance:8.3f} "
                  f"{known if known is not None else default:7}  {note}")

    for serif in (True, False):
        if results[serif]:
            print(f"\nready to paste into ADVANCE_{'SERIF' if serif else 'SANS'}:")
            line = ", ".join(f'"{c}": {v}' for c, v in sorted(results[serif].items()))
            print(f"    {line}")


MODES = ("build", "upload", "measure")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
