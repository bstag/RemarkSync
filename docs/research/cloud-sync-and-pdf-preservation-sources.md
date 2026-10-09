# Cloud sync and original-PDF preservation

Dated capability research. The recommended acquisition/export/hosting stages
below have since been implemented and used; account evidence is recorded in
[current status](../current-status.md) and the
[implementation log](implementation-log-2026-10-03.md). External evidence limits
remain distinct from live application validation. Upload work is still
[proposed](../spec/server-uploads.md).

Researched 2026-10-03. Primary sources checked online. This note establishes external capabilities, not successful integration with this account or tablet. Recommendations and untested steps are identified explicitly.

## Cloud access without connecting the tablet to the PC

**Confirmed:** reMarkable automatically syncs files when the tablet has Wi-Fi. The tablet does not need a USB cable or a direct network connection to the PC for the official cloud route. The official apps and web app offer document transfer. [reMarkable: Importing and exporting files](https://support.remarkable.com/articles/Knowledge/importing-and-exporting-files)

**Confirmed:** cloud sync is available without Connect, but inactive documents stop syncing to desktop/mobile apps after more than 50 days without being opened and synced. Connect provides unlimited cloud storage/sync. The tablet's local notes remain stored locally. An archive service must not treat a document disappearing from a limited cloud view as permission to delete its archive. [reMarkable: Sync troubleshooting](https://support.remarkable.com/articles/Knowledge/Help-with-sync-and-the-reMarkable-cloud), [reMarkable: Without a subscription](https://support.remarkable.com/articles/Knowledge/Using-reMarkable-without-a-subscription)

**Confirmed:** native Google Drive, Dropbox, and OneDrive integrations import copies into My files and export documents as PDFs. The documented export involves long-pressing a file, choosing Export, and selecting an integration; single-page export is also available. These instructions establish a useful manual preservation route, not an automatically maintained raw notebook mirror. [reMarkable: Integrations](https://support.remarkable.com/articles/Knowledge/Integrations)

**Evidence limit:** no documented, vendor-supported public API/SDK for downloading the raw document archive was found in the official sources checked. That is a search result, not proof that no partner/private API exists. The raw automatic route below is a community client and must be described that way.

## Community cloud connector

**Confirmed:** the original `juruen/rmapi` repository was archived on 2024-07-02. Its successor `ddvk/rmapi` states that it is maintained there. It offers noninteractive commands, listing and downloading by document ID, recursive downloads, Docker execution, and a configurable authentication-token file. The client is AGPL-3.0. Its `geta` annotated-PDF support is explicitly basic and limited to one pen type; it should not be the preservation renderer. [Original rmapi](https://github.com/juruen/rmapi), [Maintained rmapi README](https://github.com/ddvk/rmapi)

**Confirmed from source:** `get --id` fetches into a `.rmdoc` file. `FetchDocument` reads all entries in the document's cloud index and packages their blobs into a ZIP, with original entry names. This is a plausible transport into RemarkSync's raw-document importer. Whether every current account document contains all expected base PDF, scene, and metadata files still needs a live compatibility test. [get implementation](https://github.com/ddvk/rmapi/blob/master/shell/get.go), [sync15 FetchDocument](https://github.com/ddvk/rmapi/blob/master/api/sync15/apictx.go)

**Confirmed from current source:** the cloud index parser handles schema versions 3 and 4. Root writes default to version 4; comments explain that current servers reject new version-3 roots, and document-ID sorting addresses recent root-schema validation failures. The README's schema override is not a recommendation to force old schema 3. Pin a recent tested source revision rather than relying on an old release. [Index parsing and root serialization](https://github.com/ddvk/rmapi/blob/master/api/sync15/tree.go)

**Confirmed from source:** registration uses a pairing code; the stored device token obtains a user token. The working pairing route is `my.remarkable.com/device/browser/connect`, verified in a live browser on 2026-10-03 and used by the CLI's URL configuration. The initial `/generator-device` recommendation came from a separate auth package and returns 404; it is not the correct user-facing route. Token renewal and persistence are implemented. The file token store writes YAML tokens with mode `0600`. This permits an unattended process after initial human pairing in principle; indefinite operation, expiry/revocation behavior, and recovery must be tested. Tokens are account secrets, not an ordinary preferences file. [CLI URL configuration](https://github.com/ddvk/rmapi/blob/master/config/url.go), [Authentication source](https://github.com/ddvk/rmapi/blob/master/auth/auth.go), [Token storage source](https://github.com/ddvk/rmapi/blob/master/auth/store.go)

**Recommendation, requiring a spike:** first add a read-only cloud source adapter while continuing to run RemarkSync locally. Poll cloud revisions, download changed documents by UUID into staging, validate/extract each complete archive, and publish a version only after success. Keep source UUID, revision/hash, acquisition time, and export status. Retain prior versions and tombstones. Do not propagate deletions or uploads in the first implementation. Verify real downloaded archives against USB snapshots of the same documents.

This removes the need for a tablet-to-PC connection, but the PC still needs to run when processing occurs. To remove the need for the PC to run, move the acquisition/export worker to a small always-on container or VM with Python/native libraries, persistent state, encrypted object storage, secret storage, and authenticated access. Separate the Qt UI from that worker. This deployment recommendation is an architectural inference, not a tested hosting configuration. A serverless rewrite is unnecessary for the first version.

## Preserve highlights and notes on a copy of the original PDF

**Confirmed:** PyMuPDF can open an existing PDF and add highlight, ink, comment, and FreeText annotations, plus vector drawings and text. Highlights accept quadrilaterals; quads are preferred for angled text. Coordinates for insertion are relative to the unrotated page. Rotation/derotation matrices, crop-box position, and the PDF-to-MuPDF transformation matrix are documented. [Page API](https://pymupdf.readthedocs.io/en/latest/page.html), [Annotation recipes](https://pymupdf.readthedocs.io/en/latest/recipes-annotations.html)

**Recommendation:** preserve the original PDF bytes unchanged and create a separate annotated derivative. Overlay the tablet's rendered ink/highlights and typed notes onto the original page content rather than replacing whole pages with screenshots. Where suitable, also create native PDF highlight/ink/comment annotations, retaining searchable source text and useful PDF structure. Keep `.rmdoc`/raw files alongside the derivative: a PDF cannot preserve the full editable tablet scene. Accurate pen appearance may need vector or transparent-image overlays; standard Ink annotations alone do not guarantee tablet brush fidelity.

**Confirmed limitation:** `Document.bake()` converts visible annotations into ordinary page content, but discards underlying comments, attachments, and popups. A flattened visual edition must first render note text into visible content or include a visible notes appendix; merely baking a sticky-note icon will lose its text. `Document.save()` overwrites existing destinations without confirmation, so export code must guard the original path and publish atomically. [Document API: bake and save](https://pymupdf.readthedocs.io/en/latest/document.html#Document.bake)

**Requires a rendering spike:** derive and invert the actual tablet-to-PDF transform, including page mapping, crop/rotation, device scale, zoom/pan, and inserted notebook pages. Compare against device/official exports using real portrait, landscape, cropped, reordered, inserted-page, highlighter, handwriting, typed-text, and colored-pen fixtures. Notes extending outside the original page need an explicit policy: supplementary pages or expanded canvas. PDF/A conformance is a separate archival feature and is not implied by ordinary PDF export.

**Community page-mapping evidence:** the `remarquee` parser distinguishes UI page position from source PDF page. For modern `cPages`, it uses each page's `redir.value` for the original PDF index, excludes `deleted.value == 1`, and treats absent redirection as an inserted page (`-1`). Legacy documents use `redirectionPageMap[i]`, falling back to `i`. This is a primary implementation, not a vendor specification. It does not interpret `idx` or `original`; their complete semantics were not verified here. In particular, do not assume `original.value` is the current page's PDF index without a real fixture. Preserve the raw `.content` and resolve page identity, source page, ordering, and deletion independently before reliable export. [remarquee content parser](https://github.com/go-go-golems/remarquee/blob/main/pkg/rmdoc/content.go), [PageRef and inserted-page contract](https://pkg.go.dev/github.com/go-go-golems/remarquee/pkg/rmdoc)

**Confirmed concurrency constraint:** PyMuPDF's official guidance says it does not support operation on multiple threads and that this can produce incorrect results or crash Python. Separate document handles are not a documented exemption. The guide provides multiprocessing examples, including a Qt GUI with a separate PDF worker process. Use process isolation for concurrent PDF rendering/extraction rather than assuming a `QThread` with its own document handle is sufficient. [PyMuPDF multiprocessing guide](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)

**Confirmed licensing:** PyMuPDF/MuPDF are available under AGPL or commercial terms through Artifex. Moving processing into a distributed or hosted product warrants reviewing the actual license obligations before selecting that deployment. [PyMuPDF License and Copyright](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)

## Suggested implementation sequence

1. Implement and verify original-PDF export from existing local snapshots, preserving raw archives.
2. Prove cloud pairing, revision discovery, and full `.rmdoc` downloads with one representative annotated PDF; test renewal and invalid credentials.
3. Add the read-only cloud adapter to local RemarkSync and prove repeat sync does not duplicate or lose annotations.
4. Extract a headless worker and run it on an always-on host if PC independence is required; publish originals, raw versions, annotated PDFs, and visible flattened copies as separate artifacts.

No live account pairing, cloud download, hosting deployment, or PDF-fidelity test was performed for this source research.
