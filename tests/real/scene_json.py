"""Compare our decode against the tablet's own JSON dump of the same page.

!! Do not enable this on a device you care about. Tried on software 3.27
(Paper Pro) on 2026-09-18, SCENE_FILE_V6_DEBUG=1 does not write a harmless
side dump. It changes what goes into the page file itself: the .rm becomes the
43-byte magic, then ~314 KB of JSON, then the binary block stream. Nothing
third-party can read that -- rmscene fails on the first block header, since it
lands in the middle of the JSON -- and neither can the tablet: its own PDF
export of such a page came back a blank 1403x1873 sheet, with none of the 842
ink points on it and not even the 1620x2160 paper the page declares.

The .rm.tmp.json the log mentions is deleted when the save completes, so the
JSON has to be read out of the .rm, which is what the notes below describe.
A page left in this state should be re-saved with the flag off.

What it did tell us, from one page, is recorded in
docs/research/rmdoc-viewer-windows.md.


Every oracle so far compares *renderings*: the desktop app's thumbnails settle
where ink sits, the exported PDF settles how big the canvas is. Neither can say
what the device thinks a page *contains*, which is why three explanations for
the canvas shortfall on typed pages have died in a row.

xochitl can be asked. With ``SCENE_FILE_V6_DEBUG=1`` set it writes a JSON dump
beside each ``.rm`` as it saves it (rmscene issue #25). That is the device's own
interpretation of its own bytes, field by field.

The dump is prospective: only pages saved *after* the variable is set produce
one. So this works against a page created for the purpose rather than against
existing notes, which is better anyway -- a fixture we control and can redraw.

Current use (2026-10-03): live enablement instructions are retired. The earlier
experiment description below is historical evidence; `steps` now refuses to
provide firmware-modification commands. Offline `compare` remains available.

Usage:

    python -m tests.real.scene_json steps     # refuses retired live-debug setup
    python -m tests.real.scene_json compare   # diff the dumps against our decode

Nothing here touches the tablet. ``steps`` prints commands for a person to run,
because enabling the dump restarts xochitl and the SSH key on this machine has
a passphrase -- both belong to whoever owns the device, not to a script.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from remarksync.rmdata import decode_page

HOST = os.environ.get("REMARKSYNC_TABLET_SSH", "root@10.11.99.1")
REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl"
# What the dump is called, observed on software 3.27 (Paper Pro,
# imx8mm-ferrari): xochitl logs "Scene written to: <page>.rm.tmp.json" beside
# the .rm.tmp it is about to become. Being a .tmp it may not survive the save,
# so copy it while it is there.
DUMP_SUFFIX = ".rm.tmp.json"
CACHE = Path(os.environ.get("REMARKSYNC_SCENE_JSON", str(Path(os.environ["TEMP"]) / "rs-scene-json")))


def steps() -> None:
    """Retired after firmware debug mode produced unreadable scene files."""
    raise SystemExit(
        "Live scene-debug instructions are retired: SCENE_FILE_V6_DEBUG produced "
        "unreadable .rm files and blank device exports on software 3.27. "
        "Use only offline comparison of existing captures; see tests/real/README.md."
    )


def _summarise_ours(rm_path: Path) -> dict:
    page = decode_page(rm_path)
    ys = [y for s in page.strokes for _, y in s.points]
    xs = [x for s in page.strokes for x, _ in s.points]
    return {
        "strokes": len(page.strokes),
        "texts": len(page.texts),
        "highlights": len(page.highlights),
        "paper_size": page.paper_size,
        "x": (round(min(xs), 1), round(max(xs), 1)) if xs else None,
        "y": (round(min(ys), 1), round(max(ys), 1)) if ys else None,
        "text_widths": [round(t.width, 1) for t in page.texts],
        "text_bottoms": [round(t.pos_y + t.height, 1) for t in page.texts],
    }


def _walk(node, out: dict, depth: int = 0) -> dict:
    """Count block and item kinds in the device's dump, whatever its shape."""
    if isinstance(node, dict):
        for key in ("type", "blockType", "itemType", "kind"):
            if isinstance(node.get(key), str):
                out[f"{key}={node[key]}"] = out.get(f"{key}={node[key]}", 0) + 1
        for key in ("paperSize", "paper_size", "sceneInfo", "width", "posX", "posY"):
            if key in node and not isinstance(node[key], (dict, list)):
                out[f"field:{key}"] = node[key]
        for value in node.values():
            _walk(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _walk(value, out, depth + 1)
    return out


def compare() -> None:
    """Show the device's view of a page beside ours, so they can be diffed."""
    found = sorted(CACHE.glob("*.json")) if CACHE.exists() else []
    # xochitl writes <page-uuid>-metadata.json beside every page: 76 bytes of
    # layer names. It is not a scene dump, and it is easy to copy by accident
    # because it matches any '*.json' listing.
    layer_files = [p for p in found if p.name.endswith("-metadata.json") or p.stat().st_size < 512]
    dumps = [p for p in found if p not in layer_files]
    if layer_files:
        print(f"ignoring {len(layer_files)} file(s) that are page metadata, not scene dumps:")
        for path in layer_files[:6]:
            print(f"   {path.name} ({path.stat().st_size} bytes)")
        print()
    if not dumps:
        print(f"no scene dumps in {CACHE}.")
        print("Run 'steps' and follow step 4: the dump is not *-metadata.json,")
        print("and a v5 notebook will never produce one.")
        return
    backup = Path(os.environ.get("REMARKSYNC_REAL_BACKUP", r"C:\Backups\reMarkable\raw"))
    for dump in dumps:
        doc_uuid, _, page_name = dump.name.partition("__")
        rm_path = backup / doc_uuid / page_name.replace(".json", ".rm")
        print(f"\n=== {dump.name} ===")
        try:
            data = json.loads(dump.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            print(f"   unreadable dump: {type(exc).__name__}: {exc}")
            continue
        print("   device says:")
        for key, value in sorted(_walk(data, {}).items()):
            print(f"      {key}: {value}")
        if rm_path.exists():
            print("   we say:")
            for key, value in _summarise_ours(rm_path).items():
                print(f"      {key}: {value}")
        else:
            print(f"   no matching .rm in the backup at {rm_path}")
            print("   (pull the backup again so the same revision is compared)")


MODES = ("steps", "compare")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "steps"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
