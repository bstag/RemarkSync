# Downloading and restoring a whole-library backup

Open the private library site and choose **Download whole-library backup**.
The browser saves a dated `remarksync-library-...Z.zip`. Keep the completed
download on your PC or another independent disk. A ZIP left only on the server
does not add off-server protection.

The backup contains all retained raw document revisions, including notebooks,
folders and original PDF/EPUB data inside `.rmdoc` archives; completed preservation
PDFs, including retained corrected/earlier renderings; current publication state;
and a SHA256 manifest plus `RESTORE.txt`. It does not copy the redundant mutable
`raw` projection, search caches, temporary exports, account tokens, encryption
keys or SSH credentials. Restoration reconstructs the current raw library from
the verified archives. Credentials can be recovered separately or paired again.

The ordinary desktop downloader remains an off-server backup of revisions it
has acquired. This site download adds older server-only history to a single
portable archive. Neither method sends documents back to the tablet.

## Verify and restore offline

With the updated RemarkSync installed:

```powershell
remarksync backup verify C:/Backups/remarksync-library-DATE.zip
remarksync backup restore C:/Backups/remarksync-library-DATE.zip C:/Backups/RemarkSync-restored
```

The restore destination must be a **new directory**. Verification checks the
complete ZIP member set, each file's SHA256, the raw files inside every retained
archive, current revision references and selected PDF hashes. A failed restore
does not publish a library or overwrite an existing backup. Restoration makes
no network or account requests.

Open `C:/Backups/RemarkSync-restored/raw` in RemarkSync to browse the recovered
documents. `preservation.html` links the current PDFs; older archives/renderings
remain under `revisions/<document UUID>/<raw revision>/`. The original download
should remain separate from your working restored library. Resuming acquisition
on a replacement server requires separate initialization/account pairing.

## Download behavior and limits

The site prepares a checksum-verified list before starting, then streams ZIP64
without storing or buffering an entire archive in the web container. The library
mount remains read-only, and the web container has no account credentials. Only
one backup download/preparation runs per web process; another request receives
429 with a 15-second retry hint. Browsing and desktop downloads can continue.

Start after a successful sync. A request during acquisition, before the first
complete sync or with corrupt/missing retained artifacts receives 503; retry
after completion or investigate integrity. Acquisition can continue after the
snapshot is captured: later revisions belong to a later backup. Interrupted or
changed-source streams do not receive the completed backup manifest/ZIP footer;
verify the saved download before relying on it. Downloads cannot be resumed by
byte range, and streaming means the browser may not show a total download size.

Current safeguards allow 100,000 files, 100 GiB of backup contents, 8 GiB per
artifact, 32 MiB per metadata document and 64 MiB of captured metadata. These
are implementation limits, not statements about account size. Restoration needs
space for the retained artifacts plus the rebuilt current raw files. Raw scene
preservation does not establish complete renderer fidelity or PDF/A conformance.

Deployment verification and the actual restore drill are recorded in
[current status](current-status.md). See [data handling](security-and-data.md)
and the [server runbook](../deploy/server/README.md) for credential boundaries.
