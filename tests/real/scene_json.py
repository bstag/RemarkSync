"""Compare our decode against the tablet's own JSON dump of the same page.

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

Usage:

    python -m tests.real.scene_json steps     # what to run on the tablet
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
CACHE = Path(os.environ.get("REMARKSYNC_SCENE_JSON", str(Path(os.environ["TEMP"]) / "rs-scene-json")))


def steps() -> None:
    """Print the commands to run on the tablet, to be run by a person.

    Deliberately not automated. The tablet's SSH key here carries a
    passphrase, and enabling the dump restarts xochitl, which interrupts
    whatever is on screen. Both are decisions for whoever owns the device, so
    this prints and explains rather than doing.
    """
    print("On the tablet, over SSH. Each step is reversible; step 2 restarts")
    print("the reMarkable UI, which takes a few seconds and loses nothing.\n")
    print("1. Turn the dump on for xochitl:")
    print(f"     ssh {HOST}")
    print("     mkdir -p /etc/systemd/system/xochitl.service.d")
    print("     printf '[Service]\\nEnvironment=SCENE_FILE_V6_DEBUG=1\\n' \\")
    print("       > /etc/systemd/system/xochitl.service.d/debug.conf")
    print("     systemctl daemon-reload && systemctl restart xochitl\n")
    print("2. On the tablet itself: make a new notebook, type a few lines,")
    print("   then draw ink well below the typed text so the page overflows,")
    print("   and leave the page. That save is what writes the dump.\n")
    print("3. Check that dumps appeared, and copy them here:")
    print(f"     ssh {HOST} 'ls -t {REMOTE_DIR}/*/*.json | head'")
    print(f"     mkdir {CACHE}")
    print(f"     scp {HOST}:{REMOTE_DIR}/'<doc-uuid>/<page-uuid>.json' {CACHE}\\<doc-uuid>__<page-uuid>.json\n")
    print("4. Pull the backup so the same revision is compared:")
    print("     .venv\\Scripts\\python -m remarksync pull C:\\Backups\\reMarkable\\raw")
    print("     .venv\\Scripts\\python -m tests.real.device_pdf freshness\n")
    print("5. Then, here:")
    print("     .venv\\Scripts\\python -m tests.real.scene_json compare\n")
    print("To undo step 1:")
    print("     rm /etc/systemd/system/xochitl.service.d/debug.conf")
    print("     systemctl daemon-reload && systemctl restart xochitl")
    print("\nLeaving it on costs a JSON file beside every page you edit, so it")
    print("is worth turning off once the question is answered.")


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
    dumps = sorted(CACHE.glob("*.json")) if CACHE.exists() else []
    if not dumps:
        print(f"no dumps in {CACHE}; run the 'pull' mode first")
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
