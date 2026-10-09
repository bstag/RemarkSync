# Central acquisition preserves raw revisions and separate PDF renderings

Accepted 2026-10-03. A private server runs the existing Python acquisition and PDF
exporter continuously; desktops download its published library over private
Tailscale HTTPS. This reuses the native renderer rather than moving it into
a serverless runtime and keeps account credentials off the desktop download path.

Raw archives and earlier renderings are retained separately. A corrected PDF
can be published for an unchanged raw revision without destroying its earlier
export; a PDF derivative never replaces the editable tablet data. Flat local
libraries remain recoverable projections rather than snapshot-isolated databases.

The implemented direction is cloud acquisition and desktop download. Sending
new Markdown notebooks is the next proposed feature; editing existing cloud
documents requires a separate ownership/conflict policy. See the
[upload specification](../spec/server-uploads.md) and [current status](../current-status.md).
