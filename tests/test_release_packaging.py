"""Release scans reject private material before any upload."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('release_packager', Path(__file__).parents[1] / 'scripts/package_release.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.mark.parametrize('content', [
    '/'.join(['', 'Users', 'private-user', 'photo.raw']),
    '/'.join(['', 'home', 'private-user', 'journal.db']),
    '\\'.join(['C:', 'Users', 'private-user', 'photo.raw']),
    'github_pat_' + 'x' * 40,
])
def test_private_text_rejected(tmp_path, monkeypatch, content):
    monkeypatch.setattr(packager, 'ROOT', tmp_path)
    path = tmp_path / 'public.md'
    path.write_text(content)
    with pytest.raises(ValueError):
        packager.privacy_check([path])


def test_local_configuration_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(packager, 'ROOT', tmp_path)
    path = tmp_path / 'macos-config.json'
    path.write_text('{}')
    with pytest.raises(ValueError):
        packager.privacy_check([path])


def test_symlink_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(packager, 'ROOT', tmp_path)
    source = tmp_path / 'source.md'
    source.write_text('harmless')
    link = tmp_path / 'alias.md'
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip('Symlink permission unavailable')
    with pytest.raises(ValueError):
        packager.privacy_check([link])
