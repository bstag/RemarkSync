"""Building the measurement documents the writing oracles put on the tablet.

Every one of them works the same way: give the device a paragraph of something
repeated, in a style whose font is known, and read back where it broke the
lines. What differs is only what is repeated.

The paragraphs are handed to the compiler as styles and text, not as Markdown.
Markdown would be the shorter route but it cannot carry the fixtures: the
compiler strips ``*``, ``_`` and backticks from a line as emphasis, and reads a
leading ``#``, ``-``, ``>`` or ``1.`` as a block marker -- which is to say it
would quietly destroy or restyle exactly the characters a punctuation
measurement is there to measure.
"""

from __future__ import annotations

from pathlib import Path

from remarksync.textmetrics import STYLES

# The style used to measure each face: no indent, so the column is the whole
# text width, and no marker drawn into it. HEADING is the serif face and PLAIN
# the sans one.
FACE_STYLE = {True: "HEADING", False: "PLAIN"}


def style_for(serif: bool) -> str:
    """The name of the plain style in the face asked for, checked rather than
    trusted: these are the two the advance tables are keyed by."""
    name = FACE_STYLE[serif]
    if STYLES[name].serif is not serif or STYLES[name].indent:
        raise SystemExit(f"{name} is no longer the plain {'serif' if serif else 'sans'} style")
    return name


def compile_runs(title: str, archive: Path, plan: list[tuple[str, str]]) -> Path:
    """Write an archive of one paragraph per entry of ``plan``, which pairs a
    style name with the text to set in it."""
    from remarksync.mdcompile import build_rmdoc, paginate

    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(build_rmdoc(title, paginate(plan)))
    return archive
