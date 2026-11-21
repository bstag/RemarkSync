"""Private, read-only HTTP access to published cloud preservation artifacts.

Bind to localhost on a VPS, or publish a container port to host localhost only,
then use Tailscale Serve. No pairing, arbitrary files, or mutation endpoints.
"""
from __future__ import annotations

import hashlib
import html
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import threading
from urllib.parse import unquote, urlsplit
import uuid

from .librarybackup import snapshot, write_snapshot
from .store import HASH, LIBRARY_FORMAT, MAX_METADATA, PUBLIC_RECORD_FIELDS, STATE, open_nofollow, preservation_pdf


def _state(root: Path) -> dict:
    path = root / STATE
    if not path.is_file():
        return {"status": "not synced", "documents": {}}
    with path.open("rb") as stream:
        data = stream.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise ValueError("Sync state exceeds its size limit.")
    result = json.loads(data)
    if not isinstance(result.get("documents"), dict):
        raise ValueError("Invalid sync state.")
    return result


def _index(state: dict) -> bytes:
    rows = []
    for identifier, record in sorted(state["documents"].items(), key=lambda pair: pair[1]["name"].lower()):
        if record.get("kind") != "DocumentType":
            continue
        prefix = f'/revisions/{identifier}/{record["revision"]}/'
        pdf = f'<a href="{prefix}annotated.pdf">Open PDF</a>' if record.get("pdf_sha256") else ""
        note = "Export needs attention; raw archive preserved." if record.get("pdf_error") else ""
        if record.get("absent_since"):
            note = "Removed from the tablet; this preserved copy is kept. " + note
        if record.get("warnings"):
            note += " Export warnings are recorded in the PDF's preservation manifest."
        rows.append('<tr><td>' + html.escape(record["name"]) + '</td><td>' + pdf
                    + f'</td><td><a href="{prefix}document.rmdoc">Download raw archive</a></td>'
                    + '<td>' + html.escape(note) + '</td></tr>')
    last = html.escape(state.get("last_success") or "No complete sync yet")
    status = html.escape(state.get("status", "unknown"))
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>RemarkSync library</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;'
            'color:#20252b}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:14px;border-bottom:1px solid #ddd}'
            'a{color:#12639c}td:last-child{font-size:14px;color:#555}</style><h1>Your preserved library</h1>'
            f'<p>Sync status: {status}. Last complete sync: {last}.</p>'
            '<p>PDFs contain preserved highlights and notes. Notebooks are available as complete raw archives. '
            'Earlier revisions remain in server storage.</p>'
            '<p><a href="/backup.zip" download>Download whole-library backup</a><br>'
            '<small>ZIP includes retained document history, preservation PDFs and restoration instructions. '
            'Account credentials are excluded. Preparation may take a moment; keep the completed download separately.</small></p>'
            '<table><thead><tr><th>Document</th><th>Preservation PDF</th>'
            '<th>Original tablet data</th><th>Export notes</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></html>').encode()


def _status(state: dict) -> dict:
    report = state.get("last_report", {})
    records = state["documents"].values()
    return {"status": state.get("status"), "last_success": state.get("last_success"),
            "last_attempt": state.get("last_attempt"),
            "documents": sum(record.get("kind") == "DocumentType" for record in records),
            "pdfs": sum(bool(record.get("pdf_sha256")) for record in records),
            "removed_from_tablet": sum(bool(record.get("absent_since")) for record in records),
            "acquisition_failures": len(report.get("failed", [])), "export_failures": len(report.get("pdf_failed", []))}


def _manifest(state: dict) -> dict:
    # Explicit allowlist: never expose local paths, diagnostics or credentials.
    return {"format": LIBRARY_FORMAT, "status": state.get("status"), "last_success": state.get("last_success"),
            "documents": {key: {field: record[field] for field in PUBLIC_RECORD_FIELDS if field in record}
                          for key, record in state["documents"].items()}}


def _backup_name(created_at: str) -> str:
    """The download's filename, from the snapshot's ISO timestamp. Formatted
    rather than cut out of the string: a time on the exact second has no
    fraction to split on."""
    return f"remarksync-library-{datetime.fromisoformat(created_at).strftime('%Y%m%dT%H%M%S')}Z.zip"


def _range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", value)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    else:
        start, end = max(0, size - int(last)), size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, end, True


def handler(root: Path):
    root = root.resolve()
    backup_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "RemarkSync"
        sys_version = ""

        def log_message(self, *_args):
            pass

        def _body(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def _backup(self, state):
            if not backup_lock.acquire(blocking=False):
                self.send_response(429)
                self.send_header("Retry-After", "15")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                try:
                    plan = snapshot(root, state)
                except (OSError, ValueError, TypeError, KeyError):
                    self.send_error(503, "Backup unavailable; wait for a complete sync or check retained artifact integrity.")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{_backup_name(plan.created_at)}"')
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                # Streaming ZIP64 avoids buffering the complete library in the web container.
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                if self.command != "HEAD":
                    try:
                        self.connection.settimeout(120)
                        write_snapshot(plan, self.wfile)
                    except (OSError, ValueError):
                        # A disconnected/changed-source download has no complete ZIP footer.
                        self.close_connection = True
            finally:
                backup_lock.release()

        def do_GET(self):
            try:
                state = _state(root)
            except (OSError, ValueError):
                self.send_error(503, "Sync status is unavailable; preserved files remain on the server.")
                return
            path = unquote(urlsplit(self.path).path)
            if path == "/":
                self._body(_index(state), "text/html; charset=utf-8")
            elif path == "/backup.zip":
                self._backup(state)
            elif path == "/status":
                self._body(json.dumps(_status(state)).encode(), "application/json")
            elif path == "/manifest":
                self._body(json.dumps(_manifest(state)).encode(), "application/json")
            else:
                try:
                    self._artifact(state, path.split("/"))
                except (OSError, ValueError):
                    self.send_error(404, "Preservation artifact not found.")

        def _artifact(self, state, parts):
            """Serve one published archive or PDF, verified against its recorded
            hash first. ``ValueError``/``OSError`` mean there is no such artifact."""
            if (len(parts) != 5 or parts[:2] != ["", "revisions"] or str(uuid.UUID(parts[2])) != parts[2]
                    or not HASH.fullmatch(parts[3]) or parts[4] not in ("annotated.pdf", "document.rmdoc")):
                raise ValueError("Not an artifact route")
            identifier, revision, name = parts[2:]
            record = state["documents"].get(identifier, {})
            digest = record.get("pdf_sha256" if name == "annotated.pdf" else "archive_sha256")
            if record.get("revision") != revision or not digest:
                raise ValueError("Not a published artifact")
            target = root / "revisions" / identifier / revision
            artifact = preservation_pdf(target, record) if name == "annotated.pdf" else target / name
            with open_nofollow(root, artifact, "Not a regular artifact") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    self.send_error(503, "Artifact verification failed; server copy retained for investigation.")
                    return
                size = info.st_size
                try:
                    start, end, partial = _range(self.headers.get("Range"), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", "application/pdf" if name.endswith(".pdf") else "application/octet-stream")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                if name.endswith(".rmdoc"):
                    self.send_header("Content-Disposition", f'attachment; filename="{identifier}.rmdoc"')
                self.end_headers()
                if self.command != "HEAD":
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = stream.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

    return Handler


def serve(root: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    with ThreadingHTTPServer((host, port), handler(root)) as server:
        print(f"RemarkSync private library listening on {host}:{port}", flush=True)
        server.serve_forever()
