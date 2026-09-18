"""Reading the lines out of the tablet's own PDF export, for comparing its
line breaks against ours.

Getting the words out is not quite enough, because of ligatures. Where the
device sets a pair as one glyph, the export carries that glyph -- and only the
three with Unicode codepoints of their own (ff, fi, fl) come back as text at
all. The rest arrive unmapped, and a word-level read turns them into
whitespace or drops them, so a line that matched ours perfectly can look like
a disagreement.

Reading the glyph stream instead keeps them. Every ligature, mapped or not,
becomes one sentinel character, and the same fold applied to our own text
(where the pair is still two letters) puts both sides in the same alphabet, so
they can be compared position by position without pretending the difference is
not there.
"""

from __future__ import annotations

from pathlib import Path

from remarksync.textmetrics import Style, ligatures

# One character standing for "a ligature was set here", on both sides of the
# comparison. Not a character any note would contain.
LIGATURE = "�"
# The ligatures Unicode has codepoints for; the export carries these as text.
MAPPED = "ﬀﬁﬂﬃﬄ"
# List markers the device draws into the row with the text. Not a wrapping
# difference: the compiler puts the marker in the paragraph style, not the text.
# No space follows them here -- the marker is drawn at its own position in the
# indent, so the glyph stream runs it straight into the first word.
MARKERS = ("•", "◦", "1.", "a.", "☐", "☑")


def drawn_lines(pdf, index: int) -> list[str]:
    """The lines the tablet drew on one page, in reading order, as glyphs.

    Each ligature is one :data:`LIGATURE` character, whether or not the export
    could name it.
    """
    rows: dict[float, list[tuple[float, str]]] = {}
    for block in pdf.load_page(index).get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span["chars"]:
                    glyph = char["c"]
                    if glyph != " " and (glyph in MAPPED or glyph.isspace() or not glyph.isprintable()):
                        glyph = LIGATURE
                    # to the nearest point: a list marker sits a fraction below
                    # the text it belongs to and still shares its line
                    rows.setdefault(round(char["bbox"][3]), []).append((char["bbox"][0], glyph))
    return ["".join(g for _, g in sorted(glyphs)) for _, glyphs in sorted(rows.items())]


def canonical(line: str, s: Style | None = None) -> str:
    """A line in the alphabet both sides share: ligatures folded to one
    character, list markers dropped, ends trimmed.

    Pass the style of the line to fold the pairs the device would have ligated;
    without it only the ligatures already set as glyphs are folded, which is
    right for a line that came from the export.
    """
    for mapped in MAPPED:
        line = line.replace(mapped, LIGATURE)
    if s is not None:
        for pair in sorted(ligatures(s), key=len, reverse=True):
            line = line.replace(pair, LIGATURE)
    for marker in MARKERS:
        if line.startswith(marker):
            line = line[len(marker):]
            break
    return line.strip()


def compare(ours: list[tuple[Style, str]], theirs: list[str], label: str) -> tuple[int, int]:
    """Report where our line breaks and the tablet's differ. Returns
    (lines that agreed, lines compared)."""
    if len(ours) != len(theirs):
        print(f"   {label}: we predict {len(ours)} lines, the tablet drew {len(theirs)}")
    agreed = 0
    for (s, text), drawn in zip(ours, theirs):
        if canonical(text, s) == canonical(drawn):
            agreed += 1
        else:
            print(f"   !! ours   {canonical(text, s)[:74]!r}")
            print(f"   !! tablet {canonical(drawn)[:74]!r}")
    return agreed, min(len(ours), len(theirs))


def export_path(work: Path, uuid: str) -> Path:
    return work / f"{uuid}.pdf"
