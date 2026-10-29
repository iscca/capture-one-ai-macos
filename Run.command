#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ $# -eq 0 ]]; then
  set -- context --compact
fi
exec .venv/bin/python -m c1ai "$@"
