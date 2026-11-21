"""The tablet's USB web interface, which is how the writing oracles talk to it.

Plug the device in and it serves a small site on 10.11.99.1 for putting
documents on and taking them off. Three calls are enough for a round-trip:
send an archive, ask which id it landed under, and fetch the device's own PDF
export of it. The export is the useful part -- it says where the tablet broke
every line, which is the one thing no amount of local reasoning can settle.

Only :func:`upload` writes, and it only ever adds a notebook, which can be
deleted on the device afterwards. The other two are GETs in all but name (the
listing is a POST because the web interface made it one).
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid as uuidlib
from pathlib import Path

from .paths import HOST


def upload(archive: Path, host: str = HOST, timeout: int = 300) -> str:
    """POST an ``.rmdoc`` the way the web interface does: one multipart part
    named 'file'. Its own code accepts pdf, rmdoc and epub."""
    boundary = f"----remarksync{uuidlib.uuid4().hex}"
    content_type = mimetypes.guess_type(archive.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{archive.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        archive.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"http://{host}/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Origin": f"http://{host}", "Referer": f"http://{host}/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return f"HTTP {response.status}: {response.read(200).decode('utf-8', 'replace')}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read(400).decode('utf-8', 'replace')}"


def document_id(title: str, host: str = HOST, timeout: int = 60) -> str | None:
    """The id the tablet filed a document under, by its visible name. The
    listing covers the top level only, so a document filed into a folder on the
    device will not be found -- upload lands them at the top.

    Uploading the same fixture twice leaves two documents of the same name, and
    the device does not mind. Taking the first of them means measuring the one
    from the run before, which has happened twice and both times looked like a
    finding rather than a mistake, so the newest wins and a duplicate is said
    out loud.
    """
    request = urllib.request.Request(f"http://{host}/documents/", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        matches = [entry for entry in json.loads(response.read())
                   if entry.get("VisibleName") == title]
    if not matches:
        return None
    matches.sort(key=lambda entry: entry.get("ModifiedClient") or "", reverse=True)
    if len(matches) > 1:
        print(f"   ({len(matches)} documents on the tablet are called {title!r}; "
              f"reading the one modified {matches[0].get('ModifiedClient')}. Delete the rest.)")
    return matches[0].get("ID")


def export_pdf(uuid: str, into: Path, host: str = HOST, timeout: int = 300) -> Path:
    """The tablet's own PDF export of a document, saved as ``<uuid>.pdf``."""
    into.mkdir(parents=True, exist_ok=True)
    path = into / f"{uuid}.pdf"
    with urllib.request.urlopen(f"http://{host}/download/{uuid}/placeholder", timeout=timeout) as response:
        path.write_bytes(response.read())
    return path
