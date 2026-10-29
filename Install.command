#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This installer requires macOS."
  exit 1
fi
if [[ -x .venv/bin/python && -z "${C1AI_PYTHON:-}" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="${C1AI_PYTHON:-python3}"
fi
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required; set C1AI_PYTHON to its path"'
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/configure_macos.py "$@"
echo "Installed. Mac capabilities and limitations: docs/MACOS.md"
