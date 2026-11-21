"""CLI entry point.

    remarksync                 launch the GUI
    remarksync pull DIR        bring a raw backup up to date from the tablet (changed documents only)
    remarksync scan DIR        print the document tree of a backup
    remarksync index DIR       update the search index (--handwriting to recognise ink)
    remarksync compile MD OUT  compile Markdown to a .rmdoc
    remarksync send FILE       send Markdown or a .rmdoc to the tablet as a new document
    remarksync export SRC OUT  preserve highlights and notes in a separate PDF
    remarksync cloud sync     acquire directly from reMarkable Cloud
    remarksync server-sync    download preserved documents from the central server
    remarksync serve DIR      serve a private, read-only preservation library
    remarksync backup         verify or restore a whole-library ZIP offline
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

    p_cloud = sub.add_parser("cloud", help="read-only reMarkable Cloud sync and preservation")
    p_cloud.add_argument("operation", choices=("gui", "pair", "list", "trial", "sync", "watch"), nargs="?", default="gui")
    p_cloud.add_argument("--document", help="exact cloud document UUID for trial")
    p_cloud.add_argument("--dest", help="separate cloud store directory (defaults to output/cloud-sync)")
    p_cloud.add_argument("--pdfs-only", action="store_true", help="sync PDFs/EPUBs and folders instead of the whole library")
    p_cloud.add_argument("--interval", type=int, default=300, help="watch polling interval in seconds (minimum 30)")
    p_cloud.add_argument("--auto", action="store_true", help="enable automatic polling in the cloud GUI")

    p_serve = sub.add_parser("serve", help="read-only library access for a private Tailscale deployment")
    p_serve.add_argument("root", help="cloud store directory")
    p_serve.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)

    p_server = sub.add_parser("server-sync", help="download your central preservation library over Tailscale")
    p_server.add_argument("operation", choices=("gui", "sync", "watch"), nargs="?", default="gui")
    p_server.add_argument("--server", help="private HTTPS server address (default: $REMARKSYNC_SERVER)")
    p_server.add_argument("--dest", help="separate desktop store (defaults to output/server-sync)")
    p_server.add_argument("--interval", type=int, default=300, help="watch interval in seconds (minimum 30)")
    p_server.add_argument("--auto", action="store_true", help="enable automatic checks in the GUI")

    p_backup = sub.add_parser("backup", help="verify or restore a downloaded whole-library backup offline")
    backup_commands = p_backup.add_subparsers(dest="operation", required=True)
    p_verify = backup_commands.add_parser("verify", help="verify checksums, completeness and raw document contents")
    p_verify.add_argument("archive", help="downloaded whole-library ZIP")
    p_restore = backup_commands.add_parser("restore", help="restore into a new directory; existing libraries are refused")
    p_restore.add_argument("archive", help="downloaded whole-library ZIP")
    p_restore.add_argument("dest", help="new destination directory")

    p_export = sub.add_parser("export", help="preserve notes in a separate copy of an original PDF")
    p_export.add_argument("source", help="raw backup folder or a .rmdoc archive")
    p_export.add_argument("out", help="output PDF outside the raw backup")
    p_export.add_argument("--document", help="document UUID when source is a backup folder")
    p_export.add_argument("--overwrite", action="store_true", help="replace an existing preservation export")
    p_export.add_argument("--text-layout", choices=("pdf-column", "tablet"), default="tablet",
                          help="retain tablet layout (default), or wrap typed notes to the PDF body column")

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

    p_send = sub.add_parser("send", help="send Markdown or a .rmdoc to the tablet through reMarkable Cloud, as a new document")
    p_send.add_argument("file", type=str, help="a Markdown file (compiled first) or a .rmdoc archive")
    p_send.add_argument("--name", default=None, help="name on the tablet (defaults to the file name)")
    p_send.add_argument("--library", default=None, help="a backup folder, to compile Markdown for the tablet that wrote it")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    if args.command == "backup":
        import json
        import zipfile
        from pathlib import Path
        from .librarybackup import restore, verify
        try:
            result = (restore(Path(args.archive), Path(args.dest)) if args.operation == "restore"
                      else verify(Path(args.archive)))
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            print(f"Backup could not be verified/restored: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "serve":
        from pathlib import Path
        from .server import serve
        try:
            serve(Path(args.root), args.host, args.port)
        except KeyboardInterrupt:
            pass
        return 0

    if args.command == "server-sync":
        import json
        import time
        from pathlib import Path
        from .serversync import DEFAULT_DESTINATION, SERVER_ENV, ServerClient, default_server, sync
        from .cloud import CloudError
        try:
            destination = Path(args.dest) if args.dest else DEFAULT_DESTINATION
            if args.operation == "gui":
                from .cloudsync_dialog import run
                run(destination, automatic=args.auto, source="server", server=args.server)
                return 0
            if args.interval < 30:
                raise CloudError("Server polling interval must be at least 30 seconds.")
            server = args.server or default_server()
            if not server:
                raise CloudError(f"No server address. Pass --server HTTPS_ADDRESS or set {SERVER_ENV}.")
            client, failures = ServerClient(server), 0
            while True:
                try:
                    result = sync(client, destination, progress=lambda step:
                                  print(f"{step['done'] + 1}/{step['total']}: {step['name']}", flush=True))
                    print(json.dumps(result, indent=2), flush=True)
                    failures = 0 if result["status"] == "ok" else min(failures + 1, 4)
                except Exception:
                    if args.operation == "sync":
                        raise
                    print("Server check failed; local copies retained. Retrying after backoff.", file=sys.stderr, flush=True)
                    failures = min(failures + 1, 4)
                if args.operation == "sync":
                    return int(result["status"] != "ok")
                time.sleep(min(args.interval * 2**failures, 3600))
        except KeyboardInterrupt:
            return 0
        except CloudError as exc:
            print(str(exc), file=sys.stderr)
        except Exception:
            print("Server sync failed validation; local copies were retained.", file=sys.stderr)
        return 1

    if args.command == "cloud":
        import getpass
        import json
        from pathlib import Path
        from .cloud import CloudClient, CloudError, PAIR_URL, trial
        try:
            if args.operation == "gui":
                from .cloudsync_dialog import run
                run(Path(args.dest or "output/cloud-sync"), automatic=args.auto, source="cloud")
                return 0
            client = CloudClient()
            if args.operation in ("pair", "list"):
                code = None
                if args.operation == "pair":
                    if not sys.stdin.isatty():
                        raise CloudError("Use the cloud dialog or an interactive terminal for masked pairing.")
                    print(f"Get a one-time code from {PAIR_URL}")
                    code = getpass.getpass("Eight-character pairing code: ").strip()
                print(json.dumps(client.list(code), indent=2))
            elif args.operation in ("sync", "watch"):
                import time
                from .cloudsync import sync
                if args.interval < 30:
                    raise CloudError("Cloud polling interval must be at least 30 seconds.")
                failures = 0
                while True:
                    try:
                        result = sync(client, Path(args.dest or "output/cloud-sync"), pdfs_only=args.pdfs_only,
                                      progress=lambda step: print(f"{step['done'] + 1}/{step['total']}: {step['name']}", flush=True))
                        print(json.dumps(result, indent=2), flush=True)
                        failures = 0 if result["status"] == "ok" else min(failures + 1, 4)
                    except Exception:
                        if args.operation == "sync":
                            raise
                        print("Cloud check failed; prior files retained. Retrying after backoff.", file=sys.stderr, flush=True)
                        failures = min(failures + 1, 4)
                    if args.operation == "sync":
                        return int(result["status"] != "ok")
                    time.sleep(min(args.interval * 2**failures, 3600))
            else:
                if not args.document:
                    raise CloudError("--document UUID is required for a cloud trial")
                print(json.dumps(trial(client, args.document, Path(args.dest or "output/cloud-trial")), indent=2))
        except KeyboardInterrupt:
            return 0
        except CloudError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception:
            print("Cloud operation failed validation or service access; existing archives were retained.", file=sys.stderr)
            return 1
        return 0

    if args.command == "export":
        from pathlib import Path

        from .backup import iter_docs, open_rmdoc, scan_backup
        from .pdfexport import export_pdf

        try:
            source = Path(args.source)
            if source.is_dir():
                if not args.document:
                    raise ValueError("--document UUID is required for a backup folder")
                doc = next((d for d in iter_docs(scan_backup(source)) if d.uuid == args.document), None)
                if doc is None:
                    raise ValueError(f"document not found: {args.document}")
            else:
                doc = open_rmdoc(source)
            report = export_pdf(doc, Path(args.out), overwrite=args.overwrite, text_layout=args.text_layout)
        except Exception as exc:
            print(f"Export failed: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {report.path}: {report.pages} pages, {report.supplementary_pages} extra note/copy pages")
        for warning in report.warnings:
            print(f"  warning: {warning}")
        return 0

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
              f"{report.skipped} unchanged, {report.removed} removed, {report.failed} failed, {time.perf_counter() - t0:.0f} s; "
              f"recognitions cached: {len(library.recognition)}")
        for error in report.errors:
            print(f"  failed: {error}")
        return 1 if report.failed else 0

    if args.command == "send":
        import tempfile
        from pathlib import Path

        from .cloud import CloudClient, CloudError, send

        src = Path(args.file)
        name = args.name or src.stem
        try:
            with tempfile.TemporaryDirectory(prefix="remarksync-send-") as temporary:
                archive = src
                if src.suffix.lower() != ".rmdoc":
                    from .backup import device_paper_size
                    from .mdcompile import markdown_to_rmdoc

                    paper = device_paper_size(Path(args.library)) if args.library else None
                    archive = markdown_to_rmdoc(src.read_text(encoding="utf-8"), name, Path(temporary) / "compiled.rmdoc", paper)
                result = send(CloudClient(), archive, name)
        except (CloudError, OSError) as exc:
            print(f"not sent: {exc}")
            return 1
        print(f"'{name}' is already in the cloud with this content" if result["status"] == "already-sent" else
              f"sent '{name}'; it appears on the tablet after its next sync")
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
