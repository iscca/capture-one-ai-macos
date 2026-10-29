"""Configure Mac locally; never puts Session paths in tracked project files."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from c1ai.macos_backend import MacOSBackend
from c1ai.macos_ipc import private_directory, state_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-current-session', action='store_true')
    parser.add_argument('--allow-preview-export', action='store_true')
    parser.add_argument('--enable-experimental-modules', action='store_true')
    args = parser.parse_args()
    root = state_directory()
    private_directory(root)
    backend = MacOSBackend(root)
    context = backend.context()
    config = backend.config()
    if args.enable_current_session:
        target = context.get('target')
        if not target or target['document']['kind'] != 'session':
            parser.error('Open a Session and select one photo on its Background layer first')
        config.update(document=target['document'], version=target['instance']['version'],
                      experimental_scalar_writes=True)
    if args.enable_experimental_modules:
        if not config.get('experimental_scalar_writes'):
            parser.error('Configure a Session before enabling experimental modules')
        config['experimental_modules'] = ['curves','levels','color_mixer','color_balance','layers','masks','film_grain']
    if args.allow_preview_export:
        config['allow_preview_export'] = True
    (root / 'macos-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    mcp = {'mcpServers': {'capture-one-ai': {'command': sys.executable, 'args': ['-m', 'c1ai', 'mcp']}}}
    project = Path(__file__).resolve().parent.parent
    output = project / 'config/mcp-server.local.json'
    output.write_text(json.dumps(mcp, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps({'status': 'ok', 'mcp_config': str(output),
                      'experimental_writes': config.get('experimental_scalar_writes', False),
                      'experimental_modules': config.get('experimental_modules', []),
                      'preview_export': config.get('allow_preview_export', False)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
