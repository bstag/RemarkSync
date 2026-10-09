#!/usr/bin/env bash
set -euo pipefail
# Run from the release directory. Tests use synthetic data and temporary RAM,
# never the real pairing profile or library volumes.
sudo -n docker run --rm --read-only --memory 1g --cpus 1 \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,src=$PWD/tests,dst=/tests,readonly" \
  --tmpfs /tmp:mode=1777 --env PYTHONPATH=/tmp/testdeps \
  --entrypoint sh remarksync-server:local -c \
  'python -m pip install --disable-pip-version-check --no-cache-dir --target /tmp/testdeps "pytest>=8" "packaging>=23,<24" >/tmp/test-install.log && python -m pytest /tests/test_cloud.py /tests/test_cloudsync.py /tests/test_cloudcredentials.py /tests/test_server.py /tests/test_serversync.py /tests/test_librarybackup.py /tests/test_rmdata.py /tests/test_pdfexport.py -q -p no:cacheprovider --basetemp /tmp/pytest'
