"""JSON-in/JSON-out export subprocess used by the desktop GUI."""

import json
import sys
from contextlib import redirect_stdout


def main():
    try:
        # Library diagnostics must never corrupt the JSON wire protocol.
        with redirect_stdout(sys.stderr):
            from .pdfexport import document_from_payload, export_pdf
            request = json.load(sys.stdin)
            report = export_pdf(document_from_payload(request["document"]), request["destination"],
                                overwrite=request.get("overwrite", False), text_layout=request.get("text_layout", "tablet"))
        print(json.dumps({"path": str(report.path), "pages": report.pages,
                          "supplementary_pages": report.supplementary_pages, "warnings": report.warnings}))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
