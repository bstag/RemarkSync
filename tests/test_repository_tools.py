"""Private files stay out of deployment packages; hygiene diagnostics redact values."""
import importlib.util
from pathlib import Path
import tarfile

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_package_excludes_runtime_credentials_and_personal_documents(tmp_path):
    module = load('package-server')
    root = tmp_path / 'repo'
    files = {'README.md': 'source documentation', 'pyproject.toml': '[project]',
             'src/remarksync/cloud.py': '# application source', 'src/remarksync/account.fernet': 'private',
             'src/remarksync/.env': 'private', 'src/remarksync/personal.pdf': 'private',
             '.tools/deploy-ssh/id_ed25519': 'private', 'output/library/document.rmdoc': 'private',
             'tmp/settings.json': 'private', 'tests/test_example.py': '# synthetic test'}
    for name, value in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    report = module.package(root, root / 'tmp/release.tgz')
    with tarfile.open(report['archive']) as archive:
        assert set(archive.getnames()) == {'README.md', 'pyproject.toml', 'src/remarksync/cloud.py', 'tests/test_example.py'}
    with pytest.raises(ValueError, match='ignored'):
        module.package(root, root / 'docs/release.tgz')


def test_release_package_respects_versionable_source_set(tmp_path):
    module = load('package-server')
    root = tmp_path / 'repo'
    source = root / 'scripts'
    source.mkdir(parents=True)
    (source / 'supported.py').write_text('# safe source')
    (source / 'ignored-scratch.py').write_text('# ignored private experiment')
    report = module.package(root, root / 'tmp/release.tgz', {'scripts/supported.py'})
    with tarfile.open(report['archive']) as archive:
        assert archive.getnames() == ['scripts/supported.py']


def test_release_package_refuses_a_link_to_outside_source(tmp_path):
    module = load('package-server')
    root = tmp_path / 'repo'
    source = root / 'src'
    source.mkdir(parents=True)
    outside = tmp_path / 'outside.py'
    outside.write_text('not release source')
    try:
        (source / 'linked.py').symlink_to(outside)
    except OSError:
        pytest.skip('Symlinks unavailable')
    with pytest.raises(ValueError, match='symlink'):
        module.package(root, root / 'tmp/release.tgz')
    assert not (root / 'tmp/release.tgz').exists()


def test_secret_findings_do_not_contain_matching_values():
    module = load('check-repository-hygiene')
    value = ('ghp_' + 'a' * 40).encode()
    findings = module.findings('source.py', value)
    assert findings == [{'path': 'source.py', 'category': 'GitHub token'}]
    assert value.decode() not in str(findings)


def test_documentation_check_finds_broken_local_link_without_fetching_urls(tmp_path):
    module = load('check-repository-hygiene')
    doc = tmp_path / 'README.md'
    (tmp_path / 'real.md').write_text('# Existing')
    doc.write_text('[existing](real.md#heading) [broken](absent.md) [web](https://example.com)\n'
                   '```text\n[example](does-not-exist.md)\n```')
    count, problems = module.documentation_links(tmp_path, doc)
    assert count == 2 and problems == [{'path': 'README.md', 'category': 'broken local documentation link', 'target': 'absent.md'}]
