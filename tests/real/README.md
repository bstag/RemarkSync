# Real-data validation and manual device experiments

Reviewed 2026-10-03. These are independent renderer/compiler oracles, not central
cloud-upload features. Synthetic tests need no device; real pytest cases opt in
through environment variables and skip when prerequisites are unavailable.
Private backups, cached exports and experiment archives remain outside Git.

## Read-only pytest suite

```powershell
$env:REMARKSYNC_REAL_BACKUP = 'C:\Backups\reMarkable\raw'
$env:REMARKSYNC_DESKTOP_STORE = "$env:APPDATA\remarkable\desktop"
.venv/Scripts/python -m pytest tests/real -q
```

| File | Role/status |
| --- | --- |
| `conftest.py` | Opt-in backup/desktop-store fixtures |
| `test_desktop_parity.py` | Compares ink mass/placement against desktop render evidence |
| `test_device_pdf_parity.py` | Compares canvas bounds against cached tablet PDF exports; missing exports skip |
| `test_pdf_preservation.py` | Checks real base PDF text and raw scene/original attachments in preservation output |
| `oracle.py`, `exported.py`, `fixture.py` | Shared render/measurement/compiler helpers; keep |
| `paths.py` | The backup, desktop-store, tablet-host and work-directory defaults every helper shares |
| `compile_fixture.md` | Synthetic Markdown writing fixture; safe to version |

The historical v5 mass discrepancy (median 1.177 versus maximum 1.15) was also
reproduced against committed baseline code; it remains unresolved. Current
synthetic success does not imply all real oracles pass. Do not relax thresholds
to make a renderer change appear correct. Run appropriate real checks after
decoder/layout/text-metric/painter changes, and record prerequisites or skips.

## Manual oracle commands

| Module/modes | Effects | Decision |
| --- | --- | --- |
| `characterize parity/translucency/sheets` | Reads backup and desktop store; may write local plots/QA artifacts | Keep; historical calibration |
| `validate_oracle` | Read-only source/revision agreement and measurement sanity | Keep |
| `device_pdf freshness/fetch/compare/erasers` | Reads tablet/source data; `fetch` issues export GETs and writes local cached PDFs | Keep; no automatic live fetch in pytest |
| `compile_roundtrip build/upload/check` | `build` writes a local archive; **upload creates a tablet notebook**; `check` fetches its exported PDF | Keep; manual compiler acceptance |
| `advances build/upload/measure` | Authors glyph worksheets; **upload creates notebooks**; measure fetches/reads exports | Keep; font measurements |
| `ligatures build/upload/measure` | Authors ligature worksheets; **upload creates notebooks**; measure reads exports | Keep; font measurements |
| `text_width read/wrap` | Reads backup declarations; wrap may fetch a tablet PDF | Keep; column-width calibration |
| `tablet.py` helpers | `upload` sends a document; `document_id` lists; `export_pdf` downloads | Keep behind explicit manual experiment calls |
| `scene_json compare` | Offline comparison of existing captured dumps with raw backup | Keep only as historical forensic reader |
| `scene_json steps` | Former instructions enabled `SCENE_FILE_V6_DEBUG` and restarted xochitl | Removed; the module no longer has the mode or the instructions |

The September 2026 Paper Pro/software-3.27 compiler round trip verified editable
text and corrected line-breaking after the wrap-inset/ligature work. These
measurements are dated evidence; they are not a test of future cloud submission.
Repeated manual uploads can create same-name fixtures; clean up deliberately on
the device and verify that comparisons use the intended document revision.

The scene-debug experiment produced `.rm` files containing JSON before the
binary scene, failed third-party parsing and yielded a blank tablet PDF. The
retired mode must not re-enable that firmware flag. Keep the captured evidence
local; do not generate it again on a user's live notes to answer a format question.

## Environment and outputs

`REMARKSYNC_TABLET` selects the USB-web host (default `10.11.99.1`).
`REMARKSYNC_DEVICE_PDF` selects the export cache; `REMARKSYNC_ROUNDTRIP`,
`REMARKSYNC_ADVANCES`, `REMARKSYNC_LIGATURES`, `REMARKSYNC_SCENE_JSON` select
private work directories. Some historical helpers default to Windows `TEMP`
or `APPDATA`, fixed fixture names, or a particular device; review prerequisites
before running them elsewhere. `REMARKSYNC_WIDTH_DOC` selects the width fixture.
The retired debug helper's old `REMARKSYNC_TABLET_SSH` setting is no longer read.

Do not run manual upload modes during routine repository verification, packaging
or cloud acquisition. Downloaded/compiled `.rmdoc`, PDFs, scene bytes and logs
are personal/experimental artifacts and stay ignored.
