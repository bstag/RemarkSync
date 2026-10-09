"""Read-only ignore/secret checks for versionable files and optional Git history.

Print filenames and categories only, never matching values. This is a focused
repository check, not a claim that every possible secret format is detectable.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'GitHub token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})'),
    'AWS access ID': re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}'),
    'JWT': re.compile(rb'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'),
    'cloud token scalar': re.compile(rb'(?mi)^\s*(?:devicetoken|usertoken|RMAPI_AUTH)\s*[:=]\s*[\"\']?[A-Za-z0-9_.-]{40,}'),
}
PRIVATE_SUFFIXES = {'.key', '.pem', '.ppk', '.p12', '.pfx', '.dpapi', '.fernet', '.rmdoc', '.rmn', '.rm', '.pdf', '.epub',
                    '.metadata', '.content', '.pagedata', '.db', '.sqlite', '.sqlite3'}
PRIVATE_NAMES = {'tokens.yaml', 'tokens.yml', 'tokens.json', 'token.json', 'credentials.json', 'tree.cache',
                 'sync-state.json', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa'}
IGNORE_PROBES = ['.tools/deploy-ssh/id_ed25519', '.tools/rmapi/rmapi.exe', '.env', '.env.production',
                 'src/remarksync/.env', 'src/remarksync/cloud.key', 'tokens.yaml', 'account.dpapi', 'account.fernet',
                 'credentials/account.fernet', 'keys/cloud.key', 'settings.local.json', 'desktop.ini',
                 'personal.pdf', 'notebook.rmdoc', 'note.rm', 'backup.metadata', '.remarksync-index.db-wal',
                 '.remarksync-history/document/revision/data', 'sync-state.json', 'debug.log',
                 'output/server-sync/raw/document.content', 'tmp/deploy/desktop-settings-before-server.json',
                 '.ruff_cache/cache', 'src/remarksync/__pycache__/cloud.pyc', 'cloud.key.backup', 'account.fernet.old',
                 'id_ed25519.old', 'tokens.json', 'token.json', 'credentials.json', 'private.ppk',
                 '.aws/credentials', 'service-account-production.json', 'src/keys/opaque.bin',
                 'src/credentials/opaque.bin', 'src/secrets/opaque.bin', 'tokens.json.old', 'credentials.json.backup']
PUBLIC_PROBES = ['.env.example', 'pyproject.toml', 'README.md', 'docs/current-status.md',
                 'scripts/check-repository-hygiene.py', 'deploy/server/compose.yaml', 'tests/real/compile_fixture.md']


def git(*args, data=None):
    # Rely on the repository's own rules, independently of personal Git ignores.
    result = subprocess.run(['git', '-c', 'core.excludesFile=NUL' if os.name == 'nt' else
                             'core.excludesFile=/dev/null', *args], cwd=ROOT, input=data, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('Git inspection failed; no repository files were modified.')
    return result.stdout


def findings(name: str, data: bytes):
    result = []
    path = Path(name)
    if (path.suffix.lower() in PRIVATE_SUFFIXES or path.name in PRIVATE_NAMES
            or re.search(r'\.(?:key|pem|dpapi|fernet)\.', path.name)
            or path.name.startswith(('tokens.json.', 'token.json.', 'credentials.json.'))
            or path.name.startswith(('id_rsa.', 'id_ed25519.', 'id_ecdsa.', 'id_dsa.', 'service-account'))
            or any(part in ('.ssh', '.aws', 'credentials', 'secrets', 'keys') for part in path.parts)
            or (path.name.startswith('.env') and path.name not in ('.env.example', '.env.sample', '.env.template'))):
        result.append({'path': name, 'category': 'private document/credential filename'})
    for category, pattern in PATTERNS.items():
        if pattern.search(data):
            result.append({'path': name, 'category': category})
    return result


def documentation_links(root: Path, path: Path):
    """Check local file/directory links; external URLs and anchors stay dated evidence."""
    count, problems = 0, []
    content = path.read_text(encoding='utf-8')
    # Links in this repository use ordinary Markdown without space-containing
    # targets; code-fence example links are intentionally ignored.
    content = re.sub(r'```[^\n]*\n[\s\S]*?```', '', content)
    for target in re.findall(r'!?\[[^\]]*\]\(([^\s)]+)\)', content):
        target = target.strip('<>')
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or not parts.path:
            continue
        count += 1
        resolved = (path.parent / unquote(parts.path)).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
            problems.append({'path': path.relative_to(root).as_posix(), 'category': 'broken local documentation link',
                             'target': parts.path})
    return count, problems


def check(history=False):
    problems = []
    probes = IGNORE_PROBES + PUBLIC_PROBES
    ignored = set(git('check-ignore', '--no-index', '-z', '--stdin',
                      data=('\0'.join(probes) + '\0').encode()).decode().split('\0'))
    problems.extend({'path': name, 'category': 'missing ignore rule'} for name in IGNORE_PROBES if name not in ignored)
    problems.extend({'path': name, 'category': 'source/documentation incorrectly ignored'} for name in PUBLIC_PROBES if name in ignored)
    names = set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')) - {''}
    inspected, links = 0, 0
    for name in sorted(names):
        path = ROOT / name
        if path.is_symlink():
            problems.append({'path': name, 'category': 'versionable symlink needs review'})
        elif path.is_file():
            inspected += 1
            if path.stat().st_size > 16 * 1024**2:
                problems.append({'path': name, 'category': 'large versionable file needs review'})
            else:
                problems.extend(findings(name, path.read_bytes()))
                if path.suffix == '.md':
                    count, broken = documentation_links(ROOT, path)
                    links += count
                    problems.extend(broken)
    # Inspect HEAD/index blobs as well: ignoring a file never removes an existing
    # committed or staged secret. --history additionally checks reachable blobs.
    objects = {}
    if history:
        for line in git('rev-list', '--objects', '--all').decode().splitlines():
            oid, _, name = line.partition(' ')
            if name:
                objects[oid] = name
    for line in git('ls-files', '--stage').decode().splitlines():
        meta, name = line.split('\t', 1)
        objects[meta.split()[1]] = name
    blob_count = 0
    if objects:
        raw = git('cat-file', '--batch-check', data=('\n'.join(objects) + '\n').encode())
        blobs = []
        for line in raw.decode().splitlines():
            oid, kind, size = line.split()
            if kind == 'blob':
                if int(size) > 16 * 1024**2:
                    problems.append({'path': objects[oid], 'category': 'large Git blob needs review'})
                else:
                    blobs.append(oid)
        raw = git('cat-file', '--batch', data=('\n'.join(blobs) + '\n').encode())
        offset = 0
        for oid in blobs:
            end = raw.index(b'\n', offset)
            size = int(raw[offset:end].split()[2])
            data = raw[end + 1:end + 1 + size]
            offset = end + 2 + size
            blob_count += 1
            for item in findings(objects[oid], data):
                problems.append(dict(item, git_object=oid[:12]))
    return {'versionable_files_checked': inspected, 'git_blobs_checked': blob_count,
            'reachable_history_checked': history, 'ignore_probes_checked': len(probes),
            'local_documentation_links_checked': links, 'findings': problems}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='also scan all reachable Git-history blobs')
    args = parser.parse_args()
    report = check(args.history)
    print(json.dumps(report, indent=2))
    return int(bool(report['findings']))


if __name__ == '__main__':
    raise SystemExit(main())
