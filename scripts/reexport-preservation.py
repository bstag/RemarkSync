"""Regenerate one cloud-store PDF offline, retaining all earlier bytes.

Stop the acquisition worker first. The destination, document UUID and current
raw revision must be explicit; this utility needs no account credentials.
"""
import argparse
import json
from pathlib import Path

from remarksync.cloudsync import reexport_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library', type=Path, help='cloud store, not its raw subdirectory or the desktop download store')
    parser.add_argument('--document', required=True, help='document UUID')
    parser.add_argument('--revision', required=True, help='currently published raw revision hash')
    args = parser.parse_args()
    print(json.dumps(reexport_pdf(args.library, args.document, args.revision), indent=2))


if __name__ == '__main__':
    main()
