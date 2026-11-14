#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
python=${GRETL_J2_PYTHON:-.local/j2-venv/bin/python}
"$python" scripts/j2-e2e.py --standalone --restart --examples \
  --port "${GRETL_STANDALONE_PORT:-18083}" --evidence "$1"
