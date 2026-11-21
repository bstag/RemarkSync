"""Compare our decode against the tablet's own JSON dump of the same page.

A historical forensic reader. On 2026-09-18 xochitl was run once with
``SCENE_FILE_V6_DEBUG=1`` (software 3.27, Paper Pro) to see what the device
thinks a page contains. It does not write a harmless side dump: the JSON goes
into the page file itself, which then nothing can read, the tablet's own PDF
export included. What that one capture told us, and why the flag must not be
set again on notes anyone cares about, is recorded in
docs/research/rmdoc-viewer-windows.md and tests/real/README.md.

Nothing here touches the tablet, and there are no instructions for producing
new dumps. This only reads captures that already exist, named
``<document-uuid>__<page-uuid>.json`` in ``REMARKSYNC_SCENE_JSON``, beside the
same page in the raw backup.

    python -m tests.real.scene_json            # diff the dumps against our decode
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from remarksync.rmdata import decode_page

from .paths import RAW, work_dir

CACHE = work_dir("REMARKSYNC_SCENE_JSON", "rs-scene-json")


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


def _walk(node, out: dict) -> dict:
    """Count block and item kinds in the device's dump, whatever its shape."""
    if isinstance(node, dict):
        for key in ("type", "blockType", "itemType", "kind"):
            if isinstance(node.get(key), str):
                out[f"{key}={node[key]}"] = out.get(f"{key}={node[key]}", 0) + 1
        for key in ("paperSize", "paper_size", "sceneInfo", "width", "posX", "posY"):
            if key in node and not isinstance(node[key], (dict, list)):
                out[f"field:{key}"] = node[key]
        for value in node.values():
            _walk(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk(value, out)
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
        print("This reads existing captures only; see tests/real/README.md for why no new ones are made.")
        return
    for dump in dumps:
        doc_uuid, _, page_name = dump.name.partition("__")
        rm_path = RAW / doc_uuid / page_name.replace(".json", ".rm")
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


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if mode != "compare":
        raise SystemExit(f"unknown mode {mode!r}; the only mode is 'compare' (live scene-debug steps are retired)")
    compare()
