"""Create a small, allowlisted release without touching the active C1 bridge."""
from __future__ import annotations
import hashlib
import argparse
import json
from pathlib import Path
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FILES = [
    '.gitignore', '.gitattributes', 'README.md', 'AGENTS.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
    'pyproject.toml', 'requirements-runtime.txt', 'Install.ps1', 'Start-CaptureOne.ps1', 'Run.ps1',
    'config/native-profile.json', 'config/mcp-server.example.json',
    'docs/INSTALL.md', 'docs/AI_CLIENTS.md', 'docs/API.md', 'docs/COLOR_API.md',
    'docs/NAVIGATION_PREVIEW.md', 'docs/SUPPORT.md', 'docs/ARCHITECTURE.md',
    'docs/DEVELOPMENT.md', 'docs/MAC_PORT.md', 'docs/RELEASE_CHECKS.md',
    'internal/StartupHook.cs', 'internal/NativeEngine.cs', 'internal/start.ps1', 'internal/build.ps1',
    'scripts/check_connection.py', 'scripts/configure.py', 'scripts/package_release.py',
    'scripts/check_release.py', 'Install.command', 'Run.command', 'scripts/configure_macos.py',
    'scripts/confirm_macos_candidate.py', 'docs/MACOS.md', 'docs/MAC_VALIDATION.md',
    'docs/SKILL.md', 'docs/RELEASE_0.3.0a1.md', 'docs/RELEASE_0.3.0a2.md',
    'skills/capture-one-edit/SKILL.md', 'skills/capture-one-edit/agents/openai.yaml',
    'skills/capture-one-edit/references/usage.md',
]


def public_files():
    paths = {ROOT / p for p in PUBLIC_FILES}
    for directory in ('c1ai', 'tests'):
        paths.update(p for p in (ROOT / directory).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.ps1', '.json', '.js'))
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path.relative_to(ROOT))
    return sorted(paths)


def privacy_check(paths):
    # Scan actual content; schema fields called token are not credentials.
    sensitive = re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,}'
                           r'|sk-[A-Za-z0-9_-]{35,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
                           r'|/(?:Users|home)/[^/\s<>\"\x27]+'
                           r'|[A-Za-z]:\\Users\\[^\\\s<>\"\x27]+')
    private_names = {'candidates.json', 'macos-config.json', 'macos-validation.json',
                     'mcp-server.local.json', 'internal-token.txt', '.env'}
    for path in paths:
        if path.is_symlink() or path.name in private_names:
            raise ValueError('Private or symlink entry: ' + str(path.relative_to(ROOT)))
        content = path.read_text(encoding='utf-8-sig')
        if sensitive.search(content):
            raise ValueError('Potential credential in ' + str(path.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['windows', 'macos', 'skill'], default='macos')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist', help='Directory for this release archives and checksums')
    args = parser.parse_args()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text('utf-8'))['project']['version']
    name = {'windows': 'CaptureOne-AI-Windows-', 'macos': 'CaptureOne-AI-macOS-Experimental-',
            'skill': 'capture-one-edit-'}[args.target] + version
    paths = public_files()
    skill_root = ROOT / 'skills/capture-one-edit'
    if args.target == 'macos':
        paths = [p for p in paths if p.suffix != '.ps1' and 'internal' not in p.relative_to(ROOT).parts
                 and p.relative_to(ROOT).as_posix() not in {'config/native-profile.json', 'scripts/configure.py', 'scripts/check_connection.py'}]
    if args.target == 'skill':
        paths = [p for p in paths if skill_root in p.parents] + [ROOT / 'LICENSE']
    privacy_check(paths)
    binaries = []
    for source in (('StartupHook', 'NativeEngine') if args.target == 'windows' else ()):
        source_hash = hashlib.sha256((ROOT / 'internal' / (source + '.cs')).read_bytes()).hexdigest()
        binary = ROOT / 'internal/bin' / source_hash[:16] / ('C1AI.' + source + '.' + source_hash[:12] + '.dll')
        if binary.is_file():
            paths.append(binary)
            binaries.append(binary.relative_to(ROOT).as_posix())
    def relative(path):
        if args.target == 'skill':
            return 'LICENSE' if path.name == 'LICENSE' else path.relative_to(skill_root).as_posix()
        return path.relative_to(ROOT).as_posix()
    manifest = {'version': version, 'target': args.target,
                'capture_one_versions': {'macos_experimental': '16.8.6.25'} if args.target != 'windows' else {'qualified': '16.8.6.3784'},
                'compiled_bridge_included': len(binaries) == 2,
                'own_bridge_assemblies': binaries,
                'files': {relative(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(paths)}}
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (name + '.zip')
    if destination.exists():
        raise FileExistsError('Release archive already exists; choose a new version or remove the unpublished archive explicitly')
    archive_root = 'capture-one-edit' if args.target == 'skill' else name
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(paths):
            archive.write(path, archive_root + '/' + relative(path))
        archive.writestr(archive_root + '/RELEASE-MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    sums = output / 'SHA256SUMS.txt'
    existing = sums.read_text('utf-8').splitlines() if sums.exists() else []
    existing = [line for line in existing if line.split('  ', 1)[-1] != destination.name]
    sums.write_text('\n'.join(existing + [digest + '  ' + destination.name]) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(destination), 'bytes': destination.stat().st_size,
                      'files': len(paths) + 1, 'sha256': digest,
                      'compiled_bridge_included': manifest['compiled_bridge_included']}))


if __name__ == '__main__':
    main()
