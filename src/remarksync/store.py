"""What the sync stores share: one place for their limits, checks and state file.

A *store* here is a folder a sync fills: ``raw`` (the current library),
``revisions`` (every retained archive and rendering, by hash) and
``sync-state.json`` (what is published). The cloud sync, the server sync, the
HTTP server and the whole-library backup all read or write that layout, and
each used to carry its own copy of these pieces.

Nothing here talks to a network or decides what to acquire.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import html
import os
from pathlib import Path
import re
import urllib.request

from .backup import safe_extract_names
from .backupstore import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_ENTRIES, StoreBusy, read_json, write_json, writer_lock

HASH = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARTIFACT = MAX_ARCHIVE_BYTES    # one archive or PDF
MAX_ENTRIES = MAX_ARCHIVE_ENTRIES   # files in one document
MAX_METADATA = 32 * 1024**2         # one JSON document: state, manifest, revision record
CLOUD_STORE_FORMAT = "remarksync-cloud-v1"
LIBRARY_FORMAT = "remarksync-library-v1"
STATE = "sync-state.json"

# The fields of a published record that may leave the machine holding it: the
# server's manifest and the whole-library backup both copy exactly these. Local
# paths, diagnostics and credentials are never among them.
PUBLIC_RECORD_FIELDS = ("document", "revision", "name", "kind", "file_type", "files",
                        "archive_sha256", "pdf_sha256", "pages", "warnings", "pdf_error", "absent_since")


class CloudError(RuntimeError):
    pass


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def copy_hashed(source, target, limit: int) -> tuple[int, str]:
    """Copy ``source`` to ``target`` in chunks; the size read and its SHA-256.

    Nothing past ``limit`` is written: the copy stops there and the returned
    size exceeds the limit, which the caller reports in its own words.
    """
    checksum, size = hashlib.sha256(), 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            break
        checksum.update(chunk)
        target.write(chunk)
    return size, checksum.hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a request carrying credentials or expecting a
    verified artifact goes to the address it was given or nowhere."""
    message = "The request was redirected unexpectedly."

    def redirect_request(self, *_args, **_kwargs):
        raise CloudError(self.message)


def linked_or_outside(root: Path, path: Path) -> bool:
    """Whether ``path`` leaves ``root`` (already resolved) or passes through a link."""
    return not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents))


def open_nofollow(root: Path, path: Path, refusal: str):
    """Open a file under ``root`` for reading, refusing links with ``ValueError``."""
    if linked_or_outside(root, path):
        raise ValueError(refusal)
    return os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")


def verify_files(raw: Path, files: list[dict]) -> bool:
    safe_extract_names([item["path"] for item in files], raw)
    return all((raw / item["path"]).is_file() and digest(raw / item["path"]) == item["sha256"] for item in files)


def revision_dir(root: Path, identifier: str, revision: str) -> Path:
    if not HASH.fullmatch(revision):
        raise CloudError("Invalid cloud revision hash.")
    target = root / "revisions" / identifier / revision
    if not target.resolve().is_relative_to(root.resolve()):
        raise CloudError("Cloud revision path escapes the sync store.")
    return target


def preservation_pdf(target: Path, record: dict) -> Path:
    """Resolve a retained rendering by hash, with legacy export compatibility."""
    checksum = record.get("pdf_sha256")
    if not isinstance(checksum, str) or not HASH.fullmatch(checksum):
        raise ValueError("Invalid preservation PDF checksum.")
    rendered = target / "renderings" / checksum / "annotated.pdf"
    return rendered if rendered.parent.exists() else target / "annotated.pdf"


def mark_absent(documents: dict[str, dict], present) -> None:
    """Note which preserved documents the source no longer lists.

    A marker and nothing more: the record and its files stay, and the marker
    goes the moment the source lists the document again. ``absent_since`` keeps
    the first time it was seen gone.
    """
    for identifier, record in documents.items():
        if identifier in present:
            record.pop("absent_since", None)
        else:
            record.setdefault("absent_since", now())


def catalog(root: Path, state: dict) -> None:
    """Write ``preservation.html``, the page linking every current preservation PDF."""
    rows = []
    for identifier, record in sorted(state["documents"].items(), key=lambda pair: pair[1]["name"].lower()):
        if not record.get("pdf_sha256"):
            continue
        path = preservation_pdf(revision_dir(root, identifier, record["revision"]), record)
        warnings = record.get("warnings", [])
        diagnostics = sum("decoder warnings" in warning for warning in warnings)
        warning = " ".join(warning for warning in warnings if "decoder warnings" not in warning)
        if diagnostics:
            warning += f" {diagnostics} pages have decoder warnings; see the PDF's preservation manifest."
        rows.append(f'<tr><td><a href="{html.escape(path.relative_to(root).as_posix(), quote=True)}">'
                    f'{html.escape(record["name"])}</a></td><td>{record.get("pages", "")}</td>'
                    f'<td>{html.escape(warning)}</td></tr>')
    content = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>RemarkSync preservation PDFs</title>'
               '<style>body{font:16px system-ui;max-width:1100px;margin:48px auto;padding:0 24px;color:#20252b}'
               'table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:16px;border-bottom:1px solid #ddd}'
               'td:last-child{font-size:14px;color:#555}a{color:#12639c}h1{font-size:30px}</style>'
               '<h1>Preservation PDFs</h1><p>Open a document to view its original PDF with preserved highlights and notes. '
               'Earlier versions and original annotation files remain in your cloud library folder.</p>'
               '<p>Placement and decoder warnings are listed below; inspect affected exports against the tablet.</p>'
               '<table><thead><tr><th>Document</th><th>Pages</th><th>Export notes</th></tr></thead><tbody>'
               + ''.join(rows) + '</tbody></table></html>')
    pending = root / "preservation.pending.html"
    pending.write_text(content, encoding="utf-8")
    os.replace(pending, root / "preservation.html")


# ------------------------------------------------- one sync run over a store

def claim_store(destination: Path, marker_name: str, occupied: str) -> tuple[Path, Path]:
    """The resolved store folder and its marker file. A folder that holds
    something but no marker belongs to something else and is refused."""
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / marker_name
    if not marker.exists() and any(root.iterdir()):
        raise CloudError(occupied)
    return root, marker


def load_state(path: Path) -> dict:
    return read_json(path) if path.exists() else {"documents": {}}


def new_report(raw_root: Path) -> dict:
    return {"downloaded": [], "changed": [], "unchanged": [], "missing": [], "failed": [], "pdf_failed": [],
            "preserved": [], "raw_library": str(raw_root), "cloud_document_writes": 0}


@contextmanager
def recording_failure(state_path: Path, state: dict):
    """A run that stops on something unexpected leaves ``status: error`` behind
    rather than a ``running`` that nothing will ever finish."""
    try:
        yield
    except Exception:
        state["status"] = "error"
        write_json(state_path, state)
        raise


def finish_sync(root: Path, state_path: Path, state: dict, result: dict, *, ok: bool) -> dict:
    """Record how a run ended (``ok`` or ``partial``) and refresh the catalog."""
    result["exports_available"] = sum(bool(record.get("pdf_sha256")) for record in state["documents"].values())
    state["status"] = "ok" if ok else "partial"
    state["last_report"] = result
    if ok:
        state["last_success"] = now()
    write_json(state_path, state)
    catalog(root, state)
    return dict(result, status=state["status"], last_success=state.get("last_success"))


STORE_BUSY = "Another RemarkSync sync is already updating this folder. Wait for it to finish, then try again."


@contextmanager
def exclusive(folder: Path, busy: str = STORE_BUSY):
    """Hold ``folder``'s writer lock. One held elsewhere is a ``CloudError``
    saying so in ``busy``, not the pull's wording about a backup."""
    with ExitStack() as stack:
        try:
            stack.enter_context(writer_lock(folder))
        except StoreBusy as exc:
            raise CloudError(busy) from exc
        yield
