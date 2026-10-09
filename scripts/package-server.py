"""Package source/tests/deployment instructions, excluding private runtime files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def package(root: Path, destination: Path, eligible_names: set[str] | None = None):
    root, destination = root.resolve(), destination.resolve()
    if not destination.is_relative_to(root / 'tmp') or not destination.name.endswith(('.tgz', '.tar.gz')):
        raise ValueError('Release archives must be written under the ignored repository tmp directory.')
    names = {'pyproject.toml', 'README.md', 'CONTEXT.md', '.gitignore', '.dockerignore', 'scripts/setup-cloud-client.py',
             'deploy/server/Dockerfile', 'deploy/server/compose.yaml'}
    for folder in ('src', 'tests', 'docs', 'scripts', 'deploy/server'):
        for path in (root / folder).rglob('*'):
            if path.suffix in ('.py', '.md', '.sh', '.ps1') and not any(part.startswith('.') or part == '__pycache__'
                                                            for part in path.relative_to(root).parts):
                names.add(path.relative_to(root).as_posix())
    if eligible_names is not None:
        names &= eligible_names
    paths = [root / name for name in sorted(names) if (root / name).is_file()]
    if any(path.is_symlink() or not path.resolve().is_relative_to(root) for path in paths):
        raise ValueError('Release source contains a symlink or a path outside the repository.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix='.release-', dir=destination.parent)
    os.close(descriptor)
    try:
        with tarfile.open(temp, 'w:gz') as out:
            for path in paths:
                out.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        os.replace(temp, destination)
    finally:
        Path(temp).unlink(missing_ok=True)
    with destination.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'archive': str(destination), 'sha256': digest, 'files': len(paths)}
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'tmp/deploy/remarksync-server.tgz')
    args = parser.parse_args()
    # Refuse credentials embedded in otherwise ordinary source before packaging.
    subprocess.run([sys.executable, str(ROOT / 'scripts/check-repository-hygiene.py')], check=True)
    # The source extension allowlist must not bring ignored scratch back into a release.
    listing = subprocess.run(['git', '-c', 'core.excludesFile=NUL' if os.name == 'nt' else
                              'core.excludesFile=/dev/null', 'ls-files', '-z', '--cached',
                              '--others', '--exclude-standard'], cwd=ROOT, capture_output=True, check=True)
    eligible = set(listing.stdout.decode().split('\0')) - {''}
    print(json.dumps(package(ROOT, args.output, eligible), indent=2))


if __name__ == '__main__':
    main()
