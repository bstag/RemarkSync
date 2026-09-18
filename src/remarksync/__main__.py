"""CLI entry point.

    remarksync                 launch the GUI
    remarksync pull DIR        bring a raw backup up to date from the tablet (changed documents only)
    remarksync scan DIR        print the document tree of a backup
    remarksync index DIR       update the search index (--handwriting to recognise ink)
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
    p_pull.add_argument("--full", action="store_true", help="fetch every document, not just changed ones")
    p_pull.add_argument("--prune", action="store_true", help="delete local documents the tablet no longer has")
    p_pull.add_argument("--dry-run", action="store_true", help="only report what a pull would fetch")

    p_scan = sub.add_parser("scan", help="print the document tree of a backup")
    p_scan.add_argument("root", type=str)

    p_index = sub.add_parser("index", help="update the search index of a backup")
    p_index.add_argument("root", type=str)
    p_index.add_argument("--handwriting", action="store_true", help="also recognise handwriting (TrOCR on the GPU if installed, else Windows ink; cached)")
    p_index.add_argument("--engine", default=None, help="handwriting engine: trocr-large-handwritten or windows-ink")
    p_index.add_argument("--rebuild", action="store_true", help="re-index every document")

    p_compile = sub.add_parser("compile", help="compile Markdown into a .rmdoc")
    p_compile.add_argument("markdown", type=str)
    p_compile.add_argument("out", type=str)
    p_compile.add_argument("--title", default=None)
    p_compile.add_argument("--library", default=None,
                           help="a backup folder, to compile for the tablet that wrote it")
    p_compile.add_argument("--paper", default=None, metavar="WxH",
                           help="page size in tablet pixels (e.g. 1620x2160); overrides --library")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    if args.command == "pull":
        from .pull import pull
        from pathlib import Path

        report = pull(Path(args.dest), host=args.host, force=args.full, prune=args.prune, dry_run=args.dry_run,
                      progress=lambda u: print(f"  fetched {u}") or True)
        state = "would pull" if args.dry_run else ("pulled" if report.completed else "pull stopped early")
        print(f"{state}: {len(report.new)} new, {len(report.changed)} changed, {len(report.unchanged)} unchanged, "
              f"{len(report.removed)} gone from the tablet{' (removed locally)' if args.prune else ''} -> {args.dest}")
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

    if args.command == "index":
        import time
        from pathlib import Path

        from .library import Library

        library = Library(Path(args.root), handwriting=args.handwriting, engine=args.engine)
        if args.handwriting and not library.handwriting:
            print("handwriting recognition is not available: install the [handwriting] extra (TrOCR) or the winrt inking package")
        elif args.handwriting:
            print(f"handwriting engine: {library.handwriting}")
        t0 = time.perf_counter()
        report = library.refresh_index(progress=lambda name: print(f"  indexed {name}") or True,
                                      force=args.rebuild)
        print(f"{'done' if report.completed else 'stopped'}: {report.indexed} documents indexed ({report.pages} pages), "
              f"{report.skipped} unchanged, {report.removed} removed, {time.perf_counter() - t0:.0f} s; "
              f"recognitions cached: {len(library.recognition)}")
        return 0

    if args.command == "compile":
        from pathlib import Path

        from .mdcompile import markdown_to_rmdoc

        from .rmdata import PAPER_SIZES

        paper = None
        if args.paper:
            try:
                width, height = (int(n) for n in args.paper.lower().split("x"))
            except ValueError:
                print(f"--paper wants a size like 1620x2160, not {args.paper!r}")
                return 2
            paper = (width, height)
        elif args.library:
            from .backup import device_paper_size

            paper = device_paper_size(Path(args.library))
            if paper is None:
                print(f"no page in {args.library} says what paper it is; using the default")

        src = Path(args.markdown)
        title = args.title or src.stem
        out = markdown_to_rmdoc(src.read_text(encoding="utf-8"), title, Path(args.out), paper)
        if paper:
            print(f"wrote {out} for {paper[0]:.0f}x{paper[1]:.0f} pages "
                  f"({PAPER_SIZES.get(tuple(paper), 'an unfamiliar tablet')})")
        else:
            print(f"wrote {out}")
        return 0

    # default: GUI
    from .gui import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
