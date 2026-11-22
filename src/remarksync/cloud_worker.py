"""Private JSON-stdin interface for the sync dialog and the sender: sync, pairing and sending."""
import contextlib
import json
from pathlib import Path
import sys

from .cloud import CloudClient, CloudError


def main() -> int:
    wire = sys.stdout
    operation = None
    try:
        request = json.load(sys.stdin)
        with contextlib.redirect_stdout(sys.stderr):
            operation = request["operation"]

            def progress(step):
                print(json.dumps({"event": "progress", **step}), file=wire, flush=True)

            if operation == "server-sync":
                from .serversync import ServerClient, sync
                result = sync(ServerClient(request["server"]), Path(request["destination"]), progress=progress)
            elif operation == "pair":
                result = {"documents": CloudClient().list(request["code"])}
            elif operation == "sync":
                from .cloudsync import sync
                result = sync(CloudClient(), Path(request["destination"]), pdfs_only=request.get("pdfs_only", False),
                              progress=progress)
            elif operation == "send":
                from .cloud import send
                result = send(CloudClient(), Path(request["archive"]), request["name"], folder=request.get("folder", ""),
                              replace=request.get("replace"), keep=Path(request["keep"]) if request.get("keep") else None)
            else:
                raise CloudError("Unsupported cloud operation.")
    except CloudError as exc:
        result = {"error": str(exc)}
    except Exception as exc:
        # No raw client/network output or secrets in general diagnostics: the
        # log gets the error's type and where it was raised, never its text.
        from . import applog
        import logging
        applog.configure("workers", rotate=False)
        logging.getLogger(__name__).error("%s stopped: %s", operation or "request", applog.where_it_failed(exc))
        result = {"error": ("Sending stopped on an unexpected error. Send again: an upload that did arrive is recognised, "
                            "not duplicated.") if operation == "send" else
                  "The cloud operation failed validation or could not read the service. Existing files were retained."}
    print(json.dumps(result))
    return int("error" in result)


if __name__ == "__main__":
    sys.exit(main())
