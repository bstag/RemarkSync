"""Install the separately distributed, checksum-pinned Linux cloud client."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import urllib.request

DIGESTS = {
    "amd64": "117616151d11937446ead6972b0934f97155443087f8406141db38fd1ac8fb25",
    "arm64": "645c170d8119b4dcb652cf79612e362fcb032dc0e5869ff520eec1324da39637",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=DIGESTS, help="explicit target architecture for container builds")
    args = parser.parse_args()
    arch = args.arch or {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if not arch or (not args.arch and platform.system() != "Linux"):
        parser.error("Use the Windows installer on Windows; Linux supports amd64 and arm64.")
    target = Path(__file__).resolve().parents[1] / ".tools" / "rmapi"
    target.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/ddvk/rmapi/releases/download/v0.0.35/rmapi-linux-{arch}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="install-", dir=target) as name:
        temporary = Path(name)
        archive = temporary / "client.tar.gz"
        size = 0
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 64 * 1024**2:
                    raise ValueError("Cloud client release exceeded its size limit.")
                stream.write(chunk)
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != DIGESTS[arch]:
                raise ValueError("Cloud client checksum mismatch; refusing to install.")
        with tarfile.open(archive) as packaged:
            executables = [entry for entry in packaged if Path(entry.name).name == "rmapi"]
            if len(executables) != 1 or not executables[0].isfile() or executables[0].size > 150 * 1024**2:
                raise ValueError("Expected one regular rmapi executable in the release.")
            executable = temporary / "rmapi"
            with packaged.extractfile(executables[0]) as source, executable.open("xb") as destination:
                shutil.copyfileobj(source, destination)
        executable.chmod(0o755)
        os.replace(executable, target / "rmapi")
    print(f"Installed pinned rmapi v0.0.35 ({arch}); SHA256 verified.")


if __name__ == "__main__":
    main()
