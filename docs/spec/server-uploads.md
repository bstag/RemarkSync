# Compile and send to tablet through the private server

Status: **proposed, not implemented**. Recorded 2026-10-03 after the user asked
whether the existing Markdown-to-`.rmdoc` workflow can send documents through
the central server. Repository housekeeping is being completed before this work.

## Current capability and intended outcome

The compiler already produces editable native-text notebook archives. Current
GUI compilation saves a local file; the user imports it manually through the
official reMarkable app or tablet USB interface. The central HTTP service is
read-only and has no upload route or cloud account mounts.

The proposed flow is:

```text
Markdown -> local compiler -> reviewed .rmdoc -> private server submission
        -> validated upload job -> reMarkable Cloud -> tablet Wi-Fi sync
        -> ordinary acquisition/preservation -> desktop download
```

Primary-source inspection of the pinned cloud client confirms `.rmdoc` as a
supported input and a `put` upload command. This is feasibility evidence;
RemarkSync has not performed a live cloud upload using its compiler output.
Sources: [supported extensions](https://github.com/ddvk/rmapi/blob/v0.0.35/util/util.go),
[put command](https://github.com/ddvk/rmapi/blob/v0.0.35/shell/put.go),
[archive upload](https://github.com/ddvk/rmapi/blob/v0.0.35/api/sync15/apictx.go).

## First-version scope

- **Compile and send to tablet** uses the same compiler and target-paper choices
  as local compilation. Keep **Compile and save** available.
- The user selects the Markdown/archive, title and cloud destination folder,
  reviews the intended result and explicitly sends it. No upload-on-save default.
- Send new documents first. Name collisions are refused or require a new name;
  `--force` overwrite/recreation must not be used automatically.
- Store submitted archive bytes/hash and job identity/status so an ambiguous
  timeout or repeated click cannot silently create duplicates.
- Show separate states for submission, cloud creation, acquisition verification
  and tablet delivery. Cloud success does not prove the tablet has synced.
- Preserve the submitted archive and ordinary acquired cloud revision. Verify
  editable text/pages against an actual tablet before declaring the feature ready.

## Service boundaries and validation

Keep the existing web service read-only and without cloud credentials. Add a
separate, private submission/worker boundary, with an explicit authenticated
sender policy and bounded uploads. Tailscale reachability alone must not be
silently treated as permission for every tailnet device to mutate the account.
The concrete authentication and queue implementation remain design decisions.

Validate path/member safety, sizes/counts, document identity, metadata/content,
page scenes and hashes. Resolve a chosen folder by stable identity using a fresh
cloud view. Serialize account operations with acquisition/token updates; do not
expose arbitrary rmapi commands or raw upstream output to the client. Account
credentials remain server-owned and pairing codes remain stdin-only.

Retries must reconcile known cloud state before repeating a write. Idempotency
cannot be assumed from an HTTP retry or a name check: test both timeout-after-
creation and concurrent submissions. A returned job status must remain readable
after restart without needing plaintext account tokens in the job store.

## Acceptance criteria

1. Compile one selected Markdown fixture and inspect its title/pages locally.
2. Submit over the private endpoint; reject invalid archives, unauthorized
   senders, oversized payloads, unsafe paths and arbitrary command arguments.
3. Create a new notebook in the selected folder and verify its acquired raw
   files; existing documents and notes remain unchanged.
4. On the tablet, verify editable text, target paper and line/page behavior;
   distinguish offline-tablet waiting from server/account failure.
5. Repeat submission and inject a timeout/restart around successful cloud
   creation; reconcile without a duplicate or an accidental overwrite.
6. Confirm ordinary central acquisition and desktop download still work and
   preserve the new archive; update status, runbook, data policy and tests.

Editing existing notebooks, merging handwriting, automatic document replacement,
general two-way sync, PDF-content replacement and automatic deletion are outside
this initial scope. They require an explicit ownership/revision/conflict policy.
