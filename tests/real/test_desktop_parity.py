"""Does RemarkSync put ink where the reMarkable desktop app puts it?

Opt-in: needs a real backup and the desktop app's store (see conftest).

Every threshold here was measured, not guessed —
``python -m tests.real.characterize parity`` prints the distributions these
come from. Observed on a 91-page corpus (2026-09):

    v6   40 inked pages   ink ratio median 1.040   centroid median  7.9 px
    v5   16 inked pages   ink ratio median 1.089   centroid median 26.8 px

v5 read 0.387 / 38.1 px before the decoder parsed the declared structure
and used per-point widths; both of those are now asserted rather than
excused.

Known and not yet explained, so deliberately outside these bounds:

* a v5 overdraw tail — worst pages run 1.5x to 3.0x the device's ink, all of
  them pages carrying strokes whose stored per-point width is 9.5 px or
  more, so the wide-stroke reading is probably still wrong for some pen;
* a v6 tail around 1.37 on two stroke-heavy pages.

Typed-text pages are excluded throughout: Qt ships no fonts, so text paints
nothing on an offscreen run and would masquerade as a decode failure.
"""

from __future__ import annotations

import statistics

import pytest

from remarksync.rmdata import decode_page

from .characterize import V5_TRANSLUCENT_THRESHOLD, is_comparable, is_v5
from .oracle import build_frame, compare, load_thumbnail, render_paper

# One tablet pixel is 1404/384 thumbnail pixels, so anything under a few
# tens of tablet px is at the oracle's resolution limit.
V6_CENTROID_LIMIT_PX = 25.0
V6_INK_RATIO_RANGE = (0.85, 1.20)
V5_INK_RATIO_RANGE = (0.85, 1.20)


@pytest.fixture(scope="session")
def measured(pairings):
    """Compare every trustworthy inked page once; reused by every test."""
    import hashlib
    from pathlib import Path

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    rows = []
    for pair in pairings:
        store = pair.thumbnail.parent.parent
        twin = store / pair.doc.uuid / f"{pair.page.rm_path.stem}.rm"
        if not twin.exists() or sha(twin) != sha(pair.page.rm_path):
            continue  # the two stores hold different revisions of this page
        if not is_comparable(decode_page(pair.page.rm_path)):
            continue  # carries typed text: no fonts offscreen, so not comparable
        thumb = load_thumbnail(pair.thumbnail)
        rows.append((pair, compare(render_paper(pair, thumb.size), thumb), "v5" if is_v5(pair) else "v6"))
    if not rows:
        pytest.skip("no trustworthy inked page to compare")
    return rows


def _subset(measured, version):
    return [(p, m) for p, m, v in measured if v == version and m.ink_theirs]


def test_the_oracle_is_a_real_oracle(measured):
    """Both stores must agree on the bytes, or we are comparing two pages."""
    assert len(measured) >= 20, "too few byte-identical inked pages to conclude anything"


def test_v6_ink_mass_matches_the_device(measured):
    """Same amount of ink on the page means pens and widths are modelled right."""
    subset = _subset(measured, "v6")
    if not subset:
        pytest.skip("no v6 pages in this corpus")
    median = statistics.median(m.ink_ratio for _, m in subset)
    low, high = V6_INK_RATIO_RANGE
    assert low <= median <= high, f"v6 ink ratio median {median:.3f} outside {V6_INK_RATIO_RANGE}"


def test_v6_ink_lands_where_the_device_puts_it(measured):
    """ADR-0001: content keeps the tablet's own position; nothing is re-centred."""
    subset = [(p, m) for p, m in _subset(measured, "v6") if m.ink_ours]
    if not subset:
        pytest.skip("no v6 pages in this corpus")
    median = statistics.median(m.centroid_delta_px for _, m in subset)
    assert median <= V6_CENTROID_LIMIT_PX, f"v6 ink centroid off by {median:.1f} tablet px"


def test_v5_ink_mass_matches_the_device(measured):
    """Guards the per-point width fix: v5 ink used to be 0.387 of the
    device's because only x and y were read from each point."""
    subset = _subset(measured, "v5")
    if not subset:
        pytest.skip("no v5 pages in this corpus")
    median = statistics.median(m.ink_ratio for _, m in subset)
    low, high = V5_INK_RATIO_RANGE
    assert low <= median <= high, f"v5 ink ratio median {median:.3f} outside {V5_INK_RATIO_RANGE}"


def test_v5_has_no_phantom_edge_strokes(measured):
    """Guards the structural parse: the tolerant scanner used to mistake
    point data for a line header and paint a 29.63 px bar across the top of
    the page, 15 points pinned to y~0 from x=0, that the device never drew."""
    offenders = []
    for pair, _, version in measured:
        if version != "v5":
            continue
        frame = build_frame(pair)
        for stroke in frame.page.strokes:
            ys = [y for _, y in stroke.points]
            xs = [x for x, _ in stroke.points]
            flat = max(ys) - min(ys) < 8.0
            at_origin = min(xs) <= 0.5 and min(ys) <= 0.5
            if flat and at_origin and stroke.width > V5_TRANSLUCENT_THRESHOLD:
                offenders.append(f"{pair.label}: {len(stroke.points)} pts, w={stroke.width:.2f}")
    assert not offenders, "phantom strokes pinned to the page edge:\n  " + "\n  ".join(offenders)


def test_our_ink_is_not_somewhere_the_device_is_blank(measured):
    """A page the device draws must not come out empty for us, and vice versa."""
    mismatched = [
        f"{pair.label}: ours {m.ink_ours * 100:.3f}% theirs {m.ink_theirs * 100:.3f}%"
        for pair, m, _ in measured
        if (m.ink_ours < 0.0005) != (m.ink_theirs < 0.0005)
    ]
    assert not mismatched, "one renderer drew nothing where the other drew ink:\n  " + "\n  ".join(mismatched)
