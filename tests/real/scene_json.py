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
# What the dump is called, observed on software 3.27 (Paper Pro,
# imx8mm-ferrari): xochitl logs "Scene written to: <page>.rm.tmp.json" beside
# the .rm.tmp it is about to become. Being a .tmp it may not survive the save,
# so copy it while it is there.
DUMP_SUFFIX = ".rm.tmp.json"
CACHE = Path(os.environ.get("REMARKSYNC_SCENE_JSON", str(Path(os.environ["TEMP"]) / "rs-scene-json")))


def steps() -> None:
    """Print the commands to run on the tablet, to be run by a person.

    Deliberately not automated. The tablet's SSH key here carries a
    passphrase, and enabling the dump restarts xochitl, which interrupts
    whatever is on screen. Both are decisions for whoever owns the device, so
    this prints and explains rather than doing.
    """
    print("Two things to know before starting, both learned the hard way:\n")
    print("  * Every page directory already holds <page-uuid>-metadata.json,")
    print("    76 bytes of layer names. That is not a dump. Any command that")
    print("    lists '*.json' will match those and look like success.")
    print("  * SCENE_FILE_V6_DEBUG concerns v6 files. A notebook from")
    print("    software 2.x stores v5 pages and will never dump, whatever is")
    print("    enabled. The page has to be newly made.\n")
    print("-" * 70)
    print("\n1. Turn the dump on. This restarts the reMarkable UI, which takes")
    print("   a few seconds and loses nothing:\n")
    print(f"     ssh {HOST}")
    print("     mkdir -p /etc/systemd/system/xochitl.service.d")
    print("     printf '[Service]\\nEnvironment=SCENE_FILE_V6_DEBUG=1\\n' \\")
    print("       > /etc/systemd/system/xochitl.service.d/debug.conf")
    print("     systemctl daemon-reload && systemctl restart xochitl\n")
    print("2. Confirm the running process actually has it, rather than")
    print("   assuming the file took effect:\n")
    print("     tr '\\0' '\\n' < /proc/$(pidof xochitl)/environ | grep SCENE_FILE")
    print("     # expect: SCENE_FILE_V6_DEBUG=1\n")
    print("3. On the tablet: create a NEW notebook. Type a few lines, then")
    print("   draw ink well below the typed text so the page overflows, then")
    print("   leave the page. Leaving it is what saves it.\n")
    print("4. Find what the save wrote. Observed on 3.27 (Paper Pro): the dump")
    print(f"   is {DUMP_SUFFIX}, a few hundred KB, beside the page:\n")
    print(f"     ls -lt {REMOTE_DIR}/*/*{DUMP_SUFFIX} | head\n")
    print("   The journal names it outright, which is the surer route if the")
    print("   suffix differs on another firmware:\n")
    print("     journalctl -u xochitl --since '-15 min' | grep rm.scenefile\n")
    print("5. Copy it, naming it so the page can be matched afterwards. Set")
    print("   the two uuids as variables so nothing has to be pasted twice:\n")
    print("     DOC=e374df20-...        # the document directory")
    print("     PAGE=62b91edb-...       # the page")
    print("     # then, from Windows:")
    print(f"     mkdir {CACHE}")
    print(f"     scp {HOST}:{REMOTE_DIR}/$DOC/$PAGE{DUMP_SUFFIX} \\")
    print(f"       {CACHE}\\${{DOC}}__${{PAGE}}.json\n")
    print("6. Pull the backup so the same revision is compared, then diff:\n")
    print("     .venv\\Scripts\\python -m remarksync pull C:\\Backups\\reMarkable\\raw")
    print("     .venv\\Scripts\\python -m tests.real.device_pdf freshness")
    print("     .venv\\Scripts\\python -m tests.real.scene_json compare\n")
    print("-" * 70)
    print("\nTo undo step 1:\n")
    print("     rm /etc/systemd/system/xochitl.service.d/debug.conf")
    print("     systemctl daemon-reload && systemctl restart xochitl\n")
    print("Worth undoing: left on it writes a dump beside every page you edit.")
    print("\nIf step 4 finds nothing, the switch does not do this on your")
    print("firmware and this route is a dead end -- say so rather than hunting.")


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
