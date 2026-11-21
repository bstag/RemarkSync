# Current repository status

Updated 2026-10-03 local time, including the 2026-10-04 UTC backup deployment.
This describes the working checkout and selected deployment, not a release tag.
Package version is `0.1.0`. Earlier reviews and experiment measurements are
linked from the [documentation index](README.md).

## Implemented and in use

```text
Tablet --Wi-Fi--> reMarkable Cloud
                         |
               private server acquisition/export (every 300 seconds)
                         |
            retained raw archives + separate preservation PDFs
                         |
                  private Tailscale HTTPS
                   /                  \
           browser library        desktop downloads
                                  /                \
                           local raw library    preserved PDFs
                           viewing/search       usable offline
```

The original SSH/USB backup workflow and direct Windows cloud acquisition also
remain supported. The central-server source is the selected workflow on this PC.

| Capability | Current behavior |
| --- | --- |
| Local library | Browse notebooks/PDFs/EPUBs from raw backup files or one archive |
| Search | SQLite full-text search; optional local handwriting recognition; decoder failures remain visible/retryable |
| SSH pull | Incremental acquisition, retained history, writer locks and recoverable publication; optional visible-library prune retains history |
| Markdown compiler | Produces editable notebook `.rmdoc` archives, with target paper from the library or explicit CLI size |
| PDF preservation | Original searchable pages retained; visible highlights, vector ink and searchable notes; supplementary sheets and embedded raw evidence |
| Typed-note reflow | Optional PDF body-column width; tablet placement is the default; highlights do not reflow |
| Direct cloud sync | Windows DPAPI or Linux encrypted profile; whole-library polling, hashes, history and export warnings |
| Hosted sync | Docker acquisition/export on the private server continues while the PC is off |
| Private web library | Read-only index/status/manifest, individual artifacts and whole-library backup ZIP; no upload endpoint |
| Whole-library backup | Retained raw revisions/completed PDFs, allowlisted metadata/checksums; offline `backup verify` and `backup restore` into a new directory |
| Desktop server sync | Downloads server archives and existing PDFs, verifies hashes/file sets, retains previous copies and works offline |
| Automatic PC checks | Every five minutes while RemarkSync is open; source/interval remembered; network failures back off |
| Tablet deletes | A document deleted on the tablet stays preserved and is marked `absent_since`; the tree hides it unless **Show Removed** is on. Added and deployed 2026-11-21 |
| Editing | Notebook typed text and page list (reorder, duplicate, add, delete) in an in-app editor; ink is never re-encoded; see below |
| Sending | From this PC through reMarkable Cloud: Markdown or an archive as a new document, or an edited notebook in place of its original; implemented 2026-11-21; one new document sent live, replacement not yet run live |

A delete on the tablet is never copied here as a delete. Each acquisition pass
marks the records the cloud no longer lists with the time that was first seen
(`absent_since`) and clears the mark if the document is listed again. The mark
travels in the server manifest to the desktop, where the document tree leaves
marked documents out and **Show Removed** lists them in a group of their own.
Files, revisions, PDFs and search results are unaffected. Deployed 2026-11-21:
the first pass on the new image marked five documents, and the next desktop
download carried all five marks to this PC.

Desktop downloads do not merge edits back to the tablet. The desktop initially
downloads current published revisions; older history already on the server
remains there. Local absence never triggers document deletion. Raw publication
uses recoverable flat files, so arbitrary readers are not snapshot-isolated.

The verified desktop copy is an off-server backup of downloaded raw archives
and preservation PDFs. It retains revisions/renderings acquired by that client
and does not propagate server disappearance into local deletion. It can be read
without the server. Its recovery point is the last successful desktop download;
it does not initially replicate all older server history or server credentials.
The site's whole-library ZIP download additionally preserves retained server-only
history. See [backup and restoration](backups.md).

## Editing and sending to the tablet

Added 2026-11-21. **Live evidence so far is one new document.** A Markdown note
sent with `remarksync send` reached the cloud under its own name, and a repeat
was refused as a duplicate name. The user confirmed it arrived on the tablet
with editable text. **Replace on tablet has had no live run**, and neither has
sending from the editor: the suite exercises both against a stand-in for the
cloud client, and the edit code was run in memory over this PC's synced library
(64 text pages rewritten with their ink bytes unchanged).

The first live attempt found a fault the stand-in could not: given a full
Windows path, the pinned client uses the whole path as the document's name.
Two notes named after a temp path were created before this was seen. Uploads
now pass a bare filename, and a send fails loudly if the document arrives under
any other name or folder. For a replacement that name is what the client
matches the old document by, so this check matters more there than here.

- **Send Markdown to Tablet…** (Ctrl+Shift+M) compiles a Markdown file and
  uploads it as a new notebook. `remarksync send FILE` does the same for a
  Markdown file or an existing `.rmdoc`.
- **Edit Document…** (Ctrl+D) opens the notebook on screen in an editor: change
  a page's typed text, reorder, duplicate, add or delete pages. Then **Save as
  .rmdoc…**, **Send to tablet as a new copy…** or **Replace on tablet…**.

Sending goes from this PC straight to reMarkable Cloud with the pinned rmapi
`put`; the tablet picks it up at its next sync. It needs this PC paired
(**Library sync… → reMarkable Cloud directly → Pair account**), which is
separate from the central server's pairing. Everything sent is kept under
`output\sent`.

What the editor will and will not touch:

- Notebooks only. Annotated PDFs and EPUBs are refused.
- A page's text is replaced by splicing a new text block into the page file;
  every other byte is left as the tablet wrote it. Ink is never re-encoded.
- A page is locked for text editing (it can still be moved, copied or deleted)
  when handwriting is pinned to its text, when it has no text block, or when it
  holds data RemarkSync cannot fully read.
- Bold/italic runs inside a paragraph are not shown in the editor and are lost
  from a page whose text is edited. Paragraph styles are kept.

What **Replace on tablet** does, and its limits:

- It is refused unless every file in the cloud still matches the copy the edit
  was made from. If the tablet has synced any change since, sync here and redo
  the edit.
- The cloud's current version is downloaded to `output\sent\replaced` first.
- The pinned client replaces by deleting the document and uploading the new
  one, as two separate steps. If the upload fails in between, the document is
  gone from the cloud until the saved version is sent again; the error names
  the file.
- Changes on the tablet that it has not synced yet are invisible to this check
  and can be lost. Let the tablet sync before replacing.
- The document keeps its name and folder. Renaming is not part of a replacement.

## How this installation has been used

1. The whole-system review found nine acquisition, extraction, mapping,
   threading, compiler, decoder, color and Paper Pro layout issues. All nine
   received fixes and regression coverage; details remain in the implementation log.
2. A cloud trial of one annotated PDF matched all 49 files against the USB
   backup, retained 338 highlight groups and 45 scenes, and verified original
   searchable text and attachment bytes.
3. A manual export was inspected for highlights, handwriting and
   its complete overflow note on a supplementary page. The typed-note column
   option was added separately. An apparent highlight/text misalignment in
   Codex's preview displayed correctly in another PDF viewer.
4. The server was paired separately through masked SSH stdin. The pairing URL
   was corrected to `/device/browser/connect`; GUI completion and Docker volume
   ownership bugs were reproduced and fixed. The existing encryption key was retained.
5. The first hosted pass acquired 44 documents, 10 folders and 12 PDFs. Restart
   checks retained pairing and reused 54 unchanged revisions without downloads.
6. A new page-2 tablet edit on an annotated PDF arrived through the ordinary poll.
   Five glyph ranges had palette `YELLOW` plus opaque-black RGBA. After the user
   confirmed yellow, only that exact contradictory encoding was corrected.
   The latest export renders yellow; the earlier gray PDF and raw revisions remain.
7. This PC downloaded the entire published library through private HTTPS. The
   next CLI, GUI child-process and actual desktop startup checks reused all 54
   entries with zero downloads or failures. The downloaded library is selected
   and automatic checks are enabled.
8. The private site gained a whole-library ZIP download. The new image passed
   synthetic Linux tests before only web was recreated. A real HTTPS download
   was verified and restored into a new offline library; current raw/PDF/original
   attachment/scene checks matched the live server. Acquisition kept its image.

Earlier current-library evidence on 2026-10-03: **44 documents, 10 folders,
54 current archives, 378 raw files, 12 PDFs/original attachments and 137 scene
attachments**. This is an observed snapshot, not a fixed expected account size.
The corrected yellow-highlight PDF's SHA256 is recorded with that revision in
the local store rather than here.

Latest hosted/restored evidence at 2026-10-04 02:12 UTC: **45 documents,
10 folders, 55 current archives, 383 raw files, 13 current PDFs/original
attachments and 138 scene attachments**. The backup additionally includes older
history: **60 retained revisions, 18 PDFs and 141 payload files** plus its
checksum manifest. The observed ZIP is 129,570,275 bytes, SHA256
`578d984d9cc2ad815481fe462069c0e148520fd4270f62e2178e8d680d4b605b`.
Its local verification evidence is under ignored `output/site-backup`, including
an isolated restored store. Counts/checksum describe that download, not every
future request. The site button and unchanged desktop manifest were also checked.

## Deployment and selected desktop configuration

Server: Ubuntu 24.04.4, x86-64, 4 vCPUs, 8 GB RAM, 60 GB storage.
Private library: `https://server.tailnet.ts.net:8443/`.
SSH: `$REMARKSYNC_SSH_HOST`. Initial acquisition/pairing release:
`~/remarksync-preservation/releases/20261003-linux-01`.
Earlier web release: `~/remarksync-preservation/releases/20261004-backup-01`.
Current release for both services: `~/remarksync-preservation/releases/20261121-removed-01`.
No public Funnel endpoint was enabled. The web service listens through
host-local `127.0.0.1:8787`; only the private Tailscale endpoint exposes it.

Both containers were recreated from one image on 2026-11-21 18:27 UTC, straight
after a completed pass, and run with `unless-stopped`. This was the first
update of the acquisition container since 2026-10-03, so it also picked up
everything built in between. The image contains the desktop's sending code;
nothing on the server invokes it and the web container still has no credentials.

| Service | Running image prefix | Contents |
| --- | --- | --- |
| `remarksync-sync-1` | `9007e0e2b60e` | Acquisition/export; marks documents the cloud no longer lists |
| `remarksync-web-1` | `9007e0e2b60e` | Manifest with removal marks, status count, whole-library backup |

The new image passed **143 Linux tests, two Windows-only skips** before the
switch; afterwards the read-only checker verified 58 documents, 11 folders, 69
archives, 484 raw files, 18 PDFs/original attachments and 143 scene attachments.
Rollback images are retained: `before-removed-marking-20261121` (`320211395287`,
previous web) and `before-central-download-20261003` (`bc72ae4c437f`, previous
sync). The VPS disk was 100% full beforehand; `docker image prune` removed 38
untagged superseded images of other applications (19.97 GB), leaving it 45%
used. No other service was stopped or changed. Watchtower leaves an old image
behind on every update, so the disk will fill again without periodic pruning. See the [server runbook](../deploy/server/README.md).

This PC: source `server`, five-minute automatic checks, destination
the checkout's `output\server-sync`, open library its `raw` subdirectory.
The original `C:\Backups\reMarkable\raw` backup and direct-cloud store remain
separate. QSettings contain preferences; credential material is stored elsewhere.
Desktop polling requires the app and Tailscale to be running. No Windows service
or login scheduled task was installed. See [settings](settings.md).

## Validation and known limits

Latest complete checkout suite: **299 passed, 19 skipped** on Windows
(31.11 seconds). The final backup memory/timeout adjustments also passed their
focused checks. The new deployed image's Linux suite has **139 passed, two
Windows-only skips**. Ruff passed across
`src`, `tests`, `scripts` and `deploy/server`. Real-data/device oracles are opt-in;
these counts do not mean every current device format has full rendering fidelity.
Repository-tool verification is recorded separately in the housekeeping record below.

Known limits remain: newer scene fields can produce decoder warnings. Across
this library that is 78 pages of one large annotated book, four of a handwriting
workbook, and one page each in three other documents.
Raw bytes remain preserved. Typed fonts are
substituted; arbitrary zoom/pan placement and full notebook-to-PDF generation
remain unverified or unsupported. The historical v5 ink-mass discrepancy was
also present in committed baseline code; no acceptance threshold was weakened.
Preservation output is not a claim of PDF/A conformance.

## Next work

Sending and editing now exist on the desktop (above) and need the rest of their first live
run: one replacement of a throwaway notebook, checked on the tablet. Sending **through the server** remains a
[proposed specification](spec/server-uploads.md); the web container still has no
cloud credentials and must retain that boundary.
Off-server document backup exists through desktop downloads; the whole-library
site ZIP additionally includes server-only history and has a successful real
offline restore drill. Full replacement-server recovery (configuration/account
pairing), backup freshness policy and optional additional storage remain follow-up.
Upload implementation, existing-document conflict handling, desktop polling
while the GUI is closed, retention policy and notebook PDF export remain pending.
See the [roadmap](research/review-fixes-and-feature-roadmap.md).

## Repository housekeeping

This pass corrects stale README/package descriptions, separates historical
evidence from current status, documents settings and supported scripts, and
expands Git/Docker exclusions for documents, credentials, runtime state and caches.
Reusable source packaging and offline PDF regeneration helpers are promoted
from local deployment scratch. Scratch scripts remain excluded and are classified
in the script inventory; no secrets or real account documents are copied into docs.
See [repository hygiene](security-and-data.md) for reproducible checks and limits.

The final read-only scan checked 107 versionable files, 325 reachable/index Git
blobs, 46 ignore/source probes and 64 local documentation links with no findings.
New tests exercise package exclusions, ignored-source filtering, symlink refusal,
redacted diagnostics and broken-link detection; Windows skips the symlink case.
The source-only packager was exercised locally, the offline repair CLI's required
arguments were checked, and the retired firmware `steps` mode refused to proceed.
That housekeeping pass changed no running service or desktop preference. The
subsequent backup feature updated only web; neither task sent a cloud document.
The post-backup documentation scan checked 111 versionable files, 346 reachable/
index Git blobs, 46 ignore/source probes and 75 local Markdown links with no findings.
