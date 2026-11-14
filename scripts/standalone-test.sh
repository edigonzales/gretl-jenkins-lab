#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
python=${GRETL_J2_PYTHON:-.local/j2-venv/bin/python}
"$python" scripts/j2-e2e.py --standalone --restart --examples \
  --port "${GRETL_STANDALONE_PORT:-18083}" --evidence "$1"
if [[ -n ${GRETL_DEV_TEST_BRANCH:-} ]]; then
  browser_python=${GRETL_BROWSER_PYTHON:-.local/j2-browser/bin/python}
  "$browser_python" scripts/j4-branches.py --standalone --port "${GRETL_STANDALONE_PORT:-18083}" \
    --branch "$GRETL_DEV_TEST_BRANCH" --evidence "$1/branches"
fi
