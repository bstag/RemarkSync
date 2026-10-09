# Repository documentation

Start with [current status](current-status.md) for implemented behavior, actual
usage, verification and the next work. [README](../README.md) is the user guide.

| Document | Purpose | Update rule |
| --- | --- | --- |
| [Current status](current-status.md) | Authoritative snapshot of this checkout and deployment | Update after a feature, deployment, confirmed limitation or validation result |
| [Roadmap](research/review-fixes-and-feature-roadmap.md) | Remaining work and acceptance criteria | Keep completed work out of the pending queue |
| [Settings](settings.md) | Defaults, actual selected configuration, environment variables and locations | Match source and observed setup; separate defaults from this PC's choices |
| [Backup and restore](backups.md) | Whole-library site download, integrity checks and offline restoration | Update scope/limits and record restore evidence in current status |
| [Data and repository hygiene](security-and-data.md) | Data flow, credentials, Git/build exclusions and checks | Update when a destination or secret boundary changes |
| [Script inventory](../scripts/README.md) | Supported setup, diagnostic and recovery scripts | Record purpose, effects, prerequisites and status for each script |
| [Server runbook](../deploy/server/README.md) | Build, pair, operate and verify the private VPS service | Record runtime/image differences and operational changes |
| [Upload specification](spec/server-uploads.md) | Proposed Markdown/archive sending workflow | Label proposed, implemented and tested separately |
| [Glossary](../CONTEXT.md) | Domain terms | Keep implementation details and progress logs elsewhere |
| [Architecture decisions](adr/) | Reasons behind consequential decisions | Amend dated decisions explicitly; preserve original measurement context |
| [Real-data validation](../tests/real/README.md) | Device oracles and experiment safety/effects | Separate read-only checks from manual device writes |
| [Implementation log](research/implementation-log-2026-10-03.md) | Historical sequence of October fixes and live trials | Preserve as dated evidence; link current status for later behavior |

Research notes capture what was checked on their stated dates. The initial
[system review](research/system-review-2026-10-03.md),
[cloud capability research](research/cloud-sync-and-pdf-preservation-sources.md),
[pinned client audit](research/cloud-trial-client-audit.md), and
[renderer research](research/rmdoc-viewer-windows.md) are evidence, not current
release status. Older test counts and recommendations in them remain historical.

Repository version is still `0.1.0`; this work has not created a release tag or
committed the accumulated feature changes. Runtime downloads, real documents,
secrets and local experiment evidence are excluded from Git.
