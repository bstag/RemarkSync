"""Incremental cloud acquisition into a separate recoverable local library.

Raw revisions are authoritative. PDF failures never discard raw acquisition,
and cloud disappearance is reported without propagating local deletions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import uuid
import zipfile

from .backup import open_rmdoc, safe_extract_names
from .backupstore import publish, recover, write_json, writer_lock
from .cloud import CloudClient, CloudError, HASH, _digest
from .pdfexport import export_pdf
from .pull import _document_entries, _move_document, _validate_document, local_signature

MARKER = ".remarksync-cloud-store.json"
STATE = "sync-state.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _catalog(root: Path, state: dict) -> None:
    rows = []
    for identifier, record in sorted(state["documents"].items(), key=lambda pair: pair[1]["name"].lower()):
        if not record.get("pdf_sha256"):
            continue
        path = preservation_pdf(_revision(root, identifier, record["revision"]), record)
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


def _verify_files(raw: Path, files: list[dict]) -> bool:
    safe_extract_names([item["path"] for item in files], raw)
    return all((raw / item["path"]).is_file() and _digest(raw / item["path"]) == item["sha256"] for item in files)


def _revision(root: Path, identifier: str, revision: str) -> Path:
    if not HASH.fullmatch(revision):
        raise CloudError("Invalid cloud revision hash.")
    target = root / "revisions" / identifier / revision
    if not target.resolve().is_relative_to(root.resolve()):
        raise CloudError("Cloud revision path escapes the sync store.")
    return target


def preservation_pdf(target: Path, record: dict) -> Path:
    """Resolve a retained rendering by hash, with legacy export compatibility."""
    digest = record.get("pdf_sha256")
    if not isinstance(digest, str) or not HASH.fullmatch(digest):
        raise ValueError("Invalid preservation PDF checksum.")
    rendered = target / "renderings" / digest / "annotated.pdf"
    return rendered if rendered.parent.exists() else target / "annotated.pdf"


def reexport_pdf(destination: Path, identifier: str, expected_revision: str) -> dict:
    """Correct one derivative offline; prior raw/PDF bytes remain immutable.

    Publish the new file first, then atomically switch metadata pointers. A
    failure between pointers leaves the previous state and its PDF readable.
    """
    if str(uuid.UUID(identifier)) != identifier or not HASH.fullmatch(expected_revision):
        raise CloudError("Invalid document identity/revision.")
    root = Path(destination).resolve()
    if json.loads((root / MARKER).read_text()).get("format") != "remarksync-cloud-v1":
        raise CloudError("Unknown cloud store format.")
    with writer_lock(root):
        state = json.loads((root / STATE).read_text())
        previous = state["documents"][identifier]
        if state.get("status") != "ok" or previous["revision"] != expected_revision:
            raise CloudError("Cloud document changed or a sync is incomplete; retry after it finishes.")
        target = _revision(root, identifier, expected_revision)
        archive = target / "document.rmdoc"
        old_pdf = preservation_pdf(target, previous)
        if _digest(archive) != previous["archive_sha256"] or _digest(old_pdf) != previous["pdf_sha256"]:
            raise CloudError("Preserved archive/PDF checksum mismatch; prior files retained.")
        with tempfile.TemporaryDirectory(prefix=".reexport-", dir=root) as temporary:
            stage = Path(temporary)
            doc = open_rmdoc(archive, stage / "source")
            if doc.uuid != identifier or not _verify_files(stage / "source", previous["files"]):
                raise CloudError("Archived source differs from its manifest; prior files retained.")
            report = export_pdf(doc, stage / "annotated.pdf", text_layout="tablet")
            digest = _digest(report.path)
            rendered = target / "renderings" / digest / "annotated.pdf"
            if not rendered.resolve().is_relative_to(target.resolve()) or any(p.is_symlink() for p in (rendered, rendered.parent, rendered.parent.parent)):
                raise CloudError("Unsafe rendering destination; prior files retained.")
            rendered.parent.mkdir(parents=True, exist_ok=True)
            if rendered.exists():
                if _digest(rendered) != digest:
                    raise CloudError("Existing rendering failed verification; retained for investigation.")
            else:
                os.link(report.path, rendered)  # atomic, refuses an existing file
            record = dict(previous, pdf_sha256=digest, pages=report.pages,
                          warnings=report.warnings, pdf_rendered_at=_now())
            record.pop("pdf_error", None)
            write_json(target / "revision.json", record)
            state["documents"][identifier] = record
            write_json(root / STATE, state)
            _catalog(root, state)
        return {"document": identifier, "revision": expected_revision,
                "previous_sha256": previous["pdf_sha256"], "pdf_sha256": digest,
                "pdf": str(rendered), "prior_pdf_retained": old_pdf.is_file()}


def _prepare(client, entry, token, root, stage):
    identifier, revision = entry["DocumentID"], entry["Hash"]
    target = _revision(root, identifier, revision)
    record_path = target / "revision.json"
    archive = target / "document.rmdoc"
    if target.exists():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["document"] != identifier or record["revision"] != revision or _digest(archive) != record["archive_sha256"]:
            raise CloudError("Existing raw revision failed verification; it was retained for investigation.")
    else:
        bundle = stage / "bundle"
        bundle.mkdir()
        incoming = bundle / "document.rmdoc"
        try:
            client.download(entry, token, incoming)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            # A long first acquisition may outlive its user token. Refresh once.
            with client.snapshot() as (latest, refreshed):
                if latest.get(identifier, {}).get("Hash") != revision:
                    raise CloudError("Cloud document changed while refreshing authorization.") from exc
                client.download(entry, refreshed, incoming)
        raw = stage / "raw"
        if entry["Metadata"].get("type") == "CollectionType":
            raw.mkdir()
            with zipfile.ZipFile(incoming) as zipped:
                safe_extract_names(zipped.namelist(), raw)
                zipped.extractall(raw)
        else:
            doc = open_rmdoc(incoming, raw)
            if doc.uuid != identifier:
                raise CloudError("Cloud archive identity differs from the selected document.")
        _validate_document(raw, identifier, local_signature(raw, identifier))
        record = {"document": identifier, "revision": revision, "acquired_at": _now(),
                  "name": entry["Metadata"].get("visibleName", "Untitled"),
                  "kind": entry["Metadata"].get("type"), "file_type": entry["Content"].get("fileType", "notebook"),
                  "files": [{"path": item["DocumentID"], "sha256": item["Hash"], "size": item["Size"]} for item in entry["Files"]],
                  "archive_sha256": _digest(incoming), "warnings": []}
        if not _verify_files(raw, record["files"]):
            raise CloudError("Extracted cloud document differs from its indexed files.")
        write_json(bundle / "revision.json", record)
        target.parent.mkdir(parents=True, exist_ok=True)
        bundle.rename(target)
    raw = stage / "raw"
    if not raw.exists():
        raw.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            safe_extract_names(zipped.namelist(), raw)
            zipped.extractall(raw)
    if not _verify_files(raw, record["files"]):
        raise CloudError("Cached cloud archive has invalid file contents.")
    _validate_document(raw, identifier, local_signature(raw, identifier))
    # A failed PDF remains retryable from the raw archive without downloading it again.
    if record["kind"] == "DocumentType" and record["file_type"] in ("pdf", "epub"):
        pdf = target / "annotated.pdf"
        if record.get("pdf_sha256"):
            pdf = preservation_pdf(target, record)
            if not pdf.is_file() or _digest(pdf) != record["pdf_sha256"]:
                raise CloudError("Existing preservation PDF failed verification; it was retained.")
        else:
            try:
                doc = open_rmdoc(archive, stage / "pdf-source")
                report = export_pdf(doc, pdf, text_layout="tablet")
                record.update(pdf_sha256=_digest(pdf), warnings=report.warnings, pages=report.pages)
                record.pop("pdf_error", None)
            except Exception as exc:
                # Raw files stay preserved, even if this renderer cannot export them.
                record["pdf_error"] = str(exc) if isinstance(exc, (CloudError, ValueError)) else "PDF export failed; raw revision retained."
            write_json(record_path, record)
    return raw, record, target


def sync(client: CloudClient, destination: Path, *, pdfs_only: bool = False, progress=None) -> dict:
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists() and any(root.iterdir()):
        raise CloudError("Choose an empty destination or an existing RemarkSync cloud store; the USB backup cannot be used here.")
    with writer_lock(root):
        if marker.exists():
            if json.loads(marker.read_text()).get("format") != "remarksync-cloud-v1":
                raise CloudError("Unknown cloud store format.")
        else:
            write_json(marker, {"format": "remarksync-cloud-v1"})
        raw_root = root / "raw"
        raw_root.mkdir(exist_ok=True)
        state_path = root / STATE
        state = json.loads(state_path.read_text()) if state_path.exists() else {"documents": {}}
        state["last_attempt"] = _now()
        state["status"] = "running"
        write_json(state_path, state)
        result = {"downloaded": [], "changed": [], "unchanged": [], "missing": [], "failed": [], "pdf_failed": [],
                  "preserved": [], "raw_library": str(raw_root), "cloud_document_writes": 0}
        try:
            with writer_lock(raw_root):
                recover(raw_root)
                with client.snapshot() as (all_remote, token):
                    remote = {key: entry for key, entry in all_remote.items() if not pdfs_only
                              or entry["Metadata"].get("type") == "CollectionType"
                              or entry["Content"].get("fileType") in ("pdf", "epub")}
                pending = []
                with tempfile.TemporaryDirectory(prefix=".sync-", dir=root) as temporary:
                    for index, (identifier, entry) in enumerate(remote.items(), 1):
                        if progress:
                            progress({"name": entry["Metadata"].get("visibleName", identifier), "done": index - 1, "total": len(remote)})
                        previous = state["documents"].get(identifier)
                        try:
                            if previous and previous["revision"] == entry["Hash"] and not previous.get("pdf_error"):
                                target = _revision(root, identifier, entry["Hash"])
                                if (_digest(target / "document.rmdoc") != previous["archive_sha256"]
                                        or (previous.get("pdf_sha256") and _digest(preservation_pdf(target, previous)) != previous["pdf_sha256"])):
                                    raise CloudError("Preserved archive/PDF checksum mismatch; previous revision retained.")
                                if _verify_files(raw_root, previous["files"]):
                                    result["unchanged"].append(identifier)
                                    continue
                            stage = Path(temporary) / identifier
                            stage.mkdir()
                            raw, record, target = _prepare(client, entry, token, root, stage)
                            pending.append((identifier, raw, record, target, previous))
                        except Exception as exc:
                            result["failed"].append({"document": identifier,
                                                     "error": str(exc) if isinstance(exc, CloudError) else "Acquisition failed validation/service access; prior revision retained."})
                    if pending:
                        with client.snapshot() as (current, _token):
                            for identifier, raw, record, target, previous in pending:
                                if current.get(identifier, {}).get("Hash") != record["revision"]:
                                    result["failed"].append({"document": identifier, "error": "Cloud source changed; revision retained for retry."})
                                    continue
                                try:
                                    publish(raw_root, raw, identifier, _document_entries, _move_document)
                                    state["documents"][identifier] = record
                                    write_json(state_path, state)
                                    result["changed" if previous else "downloaded"].append(identifier)
                                    if record.get("pdf_error"):
                                        result["pdf_failed"].append({"document": identifier, "error": record["pdf_error"]})
                                    elif record.get("pdf_sha256"):
                                        result["preserved"].append(str(preservation_pdf(target, record)))
                                except Exception:
                                    result["failed"].append({"document": identifier, "error": "Local publication failed; recovery/history retained."})
                result["missing"] = sorted(set(state["documents"]) - set(all_remote))
            result["exports_available"] = sum(bool(record.get("pdf_sha256")) for record in state["documents"].values())
            state["status"] = "partial" if result["failed"] or result["pdf_failed"] else "ok"
            state["last_report"] = result
            if state["status"] == "ok":
                state["last_success"] = _now()
            write_json(state_path, state)
            _catalog(root, state)
            return dict(result, status=state["status"], last_success=state.get("last_success"))
        except Exception:
            state["status"] = "error"
            write_json(state_path, state)
            raise
