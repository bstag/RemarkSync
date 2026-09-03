"""CLI entry point.

    remarksync                 launch the GUI
    remarksync pull DIR        pull a raw backup from the tablet into DIR
    remarksync scan DIR        print the document tree of a backup
    remarksync compile MD OUT  compile Markdown to a .rmdoc
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="remarksync")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p_pull = sub.add_parser("pull", help="pull a raw backup from the tablet over SSH")
    p_pull.add_argument("dest", type=str)
    p_pull.add_argument("--host", default="root@10.11.99.1")

    p_scan = sub.add_parser("scan", help="print the document tree of a backup")
    p_scan.add_argument("root", type=str)

    p_compile = sub.add_parser("compile", help="compile Markdown into a .rmdoc")
    p_compile.add_argument("markdown", type=str)
    p_compile.add_argument("out", type=str)
    p_compile.add_argument("--title", default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    if args.command == "pull":
        from .pull import pull
        from pathlib import Path

        dest = pull(Path(args.dest), host=args.host)
        print(f"backup pulled to {dest}")
        return 0

    if args.command == "scan":
        from pathlib import Path

        from .library import Library

        library = Library(Path(args.root))

        def show(items, indent=0):
            for it in items:
                kind = "d" if it.kind == "doc" else "f"
                extra = f" [{it.file_type}, {len(it.pages)}p]" if it.kind == "doc" else ""
                print(f"{'  ' * indent}{kind} {it.name}{extra}")
                if it.kind == "folder":
                    show(it.children, indent + 1)

        show(library.tree)
        print(f"total docs: {len(library)}")
        return 0

    if args.command == "compile":
        from pathlib import Path

        from .mdcompile import markdown_to_rmdoc

        src = Path(args.markdown)
        title = args.title or src.stem
        out = markdown_to_rmdoc(src.read_text(encoding="utf-8"), title, Path(args.out))
        print(f"wrote {out}")
        return 0

    # default: GUI
    from .gui import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
