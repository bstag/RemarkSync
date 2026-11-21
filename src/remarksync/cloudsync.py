"""Incremental cloud acquisition into a separate recoverable local library.

Raw revisions are authoritative. PDF failures never discard raw acquisition,
and cloud disappearance is reported without propagating local deletions.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import urllib.error
import uuid
import zipfile

from .backup import open_rmdoc, safe_extract_names
from .backupstore import publish, read_json, recover, write_json
from .cloud import CloudClient, CloudError
from .pdfexport import export_pdf
from .pull import _document_entries, _move_document, _validate_document, local_signature
from .store import CLOUD_STORE_FORMAT, HASH, STATE, claim_store, exclusive, finish_sync, load_state, mark_absent
from .store import new_report, preservation_pdf, recording_failure
from .store import catalog as _catalog, digest as _digest, now as _now, revision_dir as _revision
from .store import verify_files as _verify_files

# ``mark_absent``, ``preservation_pdf`` and ``STATE`` are part of this module's
# interface although they live in :mod:`remarksync.store`: scripts and the
# deployment check import them from here.
__all__ = ["DEFAULT_DESTINATION", "MARKER", "STATE", "mark_absent", "preservation_pdf", "reexport_pdf", "sync"]

MARKER = ".remarksync-cloud-store.json"
# Where the CLI and the dialog keep the cloud store unless told otherwise:
# beside the installation, never relative to wherever the command was run.
DEFAULT_DESTINATION = Path(__file__).resolve().parents[2] / "output" / "cloud-sync"


def reexport_pdf(destination: Path, identifier: str, expected_revision: str) -> dict:
    """Correct one derivative offline; prior raw/PDF bytes remain immutable.

    Publish the new file first, then atomically switch metadata pointers. A
    failure between pointers leaves the previous state and its PDF readable.
    """
    if str(uuid.UUID(identifier)) != identifier or not HASH.fullmatch(expected_revision):
        raise CloudError("Invalid document identity/revision.")
    root = Path(destination).resolve()
    if read_json(root / MARKER).get("format") != CLOUD_STORE_FORMAT:
        raise CloudError("Unknown cloud store format.")
    with exclusive(root):
        state = read_json(root / STATE)
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
        record = read_json(record_path)
        if record["document"] != identifier or record["revision"] != revision or _digest(archive) != record["archive_sha256"]:
            raise CloudError("Existing raw revision failed verification; it was retained for investigation.")
        raw = stage / "raw"
        raw.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            safe_extract_names(zipped.namelist(), raw)
            zipped.extractall(raw)
        if not _verify_files(raw, record["files"]):
            raise CloudError("Cached cloud archive has invalid file contents.")
        _validate_document(raw, identifier, local_signature(raw, identifier))
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
        # The staged files were verified against the index and validated just
        # above, before the revision was published; nothing has touched them since.
        bundle.rename(target)
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
    root, marker = claim_store(destination, MARKER, "Choose an empty destination or an existing RemarkSync cloud store; "
                               "the USB backup cannot be used here.")
    with exclusive(root):
        if marker.exists():
            if read_json(marker).get("format") != CLOUD_STORE_FORMAT:
                raise CloudError("Unknown cloud store format.")
        else:
            write_json(marker, {"format": CLOUD_STORE_FORMAT})
        raw_root = root / "raw"
        raw_root.mkdir(exist_ok=True)
        state_path = root / STATE
        state = load_state(state_path)
        state["last_attempt"] = _now()
        state["status"] = "running"
        write_json(state_path, state)
        result = new_report(raw_root)
        with recording_failure(state_path, state):
            with exclusive(raw_root):
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
                mark_absent(state["documents"], all_remote)
            return finish_sync(root, state_path, state, result, ok=not (result["failed"] or result["pdf_failed"]))
