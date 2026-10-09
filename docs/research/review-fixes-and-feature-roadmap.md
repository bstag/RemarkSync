# Remaining work and acceptance criteria

Updated 2026-10-03. [Current status](../current-status.md) records implemented
behavior and deployment. The [implementation log](implementation-log-2026-10-03.md)
preserves the full dated sequence of review fixes and live trials.

## Completed foundation

All nine findings from the initial system review have fixes and regression
coverage: recoverable raw acquisition, safe archives, source-page mapping,
PDF-threading boundaries, GUI compilation, visible/retryable decoder failures,
highlight metadata and Paper Pro placement/templates. Local PDF preservation,
direct cloud acquisition, automatic whole-library polling, private hosted
processing and server-to-desktop downloads are implemented and used. Those
verified desktop downloads already provide an off-server document/PDF backup.
The site also downloads a ZIP of retained server history with offline verify/
restore commands. A real HTTPS download and isolated restoration passed raw,
PDF and attachment integrity checks (45 documents, 10 folders, 60 revisions).

Completed operational corrections include the pairing URL, pairing completion
signal, Docker empty-volume ownership (`nocopy`), and the confirmed
yellow/opaque-black glyph placeholder. Earlier raw revisions and gray PDF
renderings remain retained. Completed features must not be presented as future
milestones in new plans.

## Next requested feature

**Compile and send to tablet via the private server** is proposed and not implemented.
The [specification](../spec/server-uploads.md) describes first-version new-document
uploads, explicit title/folder selection, a separate credential-isolated worker,
validated archives, visible job states and duplicate-safe retries.

Before marking this feature complete, verify an actual compiled notebook arrives
in the selected cloud folder and remains editable on the tablet; reconcile
ambiguous timeouts/restarts without creating duplicates; keep existing notes
untouched; and confirm ordinary acquisition, preservation and desktop downloads
still work. No document has been selected or sent by this proposed feature.

## Preservation and operational queue

| Work | Current gap | Acceptance evidence |
| --- | --- | --- |
| Replacement-server recovery and backup policy | Desktop copies and a real restored full-history ZIP are proven; complete service/account recovery and a freshness policy remain untested | Restore a replacement service in isolation, initialize credentials separately or re-pair, verify acquisition and access; choose backup cadence/destination and optional additional storage |
| Expired/revoked credentials | Renewal code exists; long-duration expiry/revocation recovery is not proven | Exercise failures safely, retain prior library, report actionable status and demonstrate re-pairing/restart recovery |
| Renderer fidelity | New scene fields, v5 mass discrepancy, font substitution and zoom/pan caveats remain | Compare representative current device/official exports and preserve diagnostics/raw bytes; do not weaken oracle thresholds |
| Dependency/image reproducibility | Minimum ranges and a mutable Python base tag; two running services intentionally use different tested image builds | Record a tested dependency snapshot/image digest and upgrade/rollback procedure; verify both services after deliberate recreation |
| Notebook PDF generation | Notebooks download as raw archives and open in the viewer | Define paper/endless-page/template/overflow behavior; validate visible notes against device exports before adding automatic rendering |
| Retention/storage policy | Earlier archives/renderings remain indefinitely | Distinguish user preservation from rebuildable caches; forecast disk use and implement any pruning with explicit preservation/recovery guarantees |
| PC polling with GUI closed | GUI polling requires RemarkSync to remain open; CLI watch requires its process | Add a deliberately selected Windows service/login task only if requested; show freshness/failure state and prevent overlap with GUI polling |
| Portable setup | Addresses, SSH host, key and release path now come from `REMARKSYNC_*` variables, so nothing is embedded; a reviewed configuration profile and a proved fresh-volume setup that does not reset an existing key remain open |
| Existing-document uploads/merging | No ownership/conflict policy | Define stable identity, expected revision, tablet-versus-PC edits and recoverable conflict handling before any replacement |

## Validation rules

- Use synthetic fixtures for failure injection and transport tests. GUI tests must
  isolate preferences and never activate the user's account/server polling.
- Real/device suites remain opt-in. Record prerequisite skips, current source
  revisions and unresolved failures rather than treating synthetic success as
  complete visual fidelity.
- Use `check-library.py` for current raw/PDF/attachment integrity, and independent
  rendering for visual inspection when the exporter/decoder changes.
- Run the repository hygiene check before packaging/committing. Never include
  real libraries, account profiles, keys, plaintext sessions or local settings
  backups in Git or build context.
- Update current status after implementation/validation, the settings/runbook
  after configuration changes, and this queue after completion. Research notes
  and the implementation log retain their dated evidence.
