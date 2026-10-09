"""Private JSON-stdin interface for the cloud trial dialog."""
import contextlib
import json
from pathlib import Path
import sys

from .cloud import CloudClient, CloudError, trial


def main() -> int:
    wire = sys.stdout
    try:
        request = json.load(sys.stdin)
        with contextlib.redirect_stdout(sys.stderr):
            operation = request["operation"]
            if operation == "server-sync":
                from .serversync import ServerClient, sync
                result = sync(ServerClient(request["server"]), Path(request["destination"]),
                              progress=lambda step: print(json.dumps({"event": "progress", **step}), file=wire, flush=True))
            elif operation == "list":
                client = CloudClient()
                result = {"documents": client.list()}
            elif operation == "pair":
                client = CloudClient()
                result = {"documents": client.list(request["code"])}
            elif operation == "trial":
                client = CloudClient()
                result = trial(client, request["document"], Path(request["destination"]))
            elif operation == "sync":
                client = CloudClient()
                from .cloudsync import sync
                result = sync(client, Path(request["destination"]), pdfs_only=request.get("pdfs_only", False),
                              progress=lambda step: print(json.dumps({"event": "progress", **step}), file=wire, flush=True))
            else:
                raise CloudError("Unsupported cloud operation.")
    except CloudError as exc:
        result = {"error": str(exc)}
    except Exception:
        # No raw client/network output or secrets in general diagnostics.
        result = {"error": "Cloud trial failed validation or could not read the service. Existing archives were retained."}
    print(json.dumps(result))
    return int("error" in result)


if __name__ == "__main__":
    sys.exit(main())
