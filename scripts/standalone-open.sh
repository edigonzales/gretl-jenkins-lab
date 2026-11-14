#!/bin/bash
set -euo pipefail
open "http://127.0.0.1:${GRETL_STANDALONE_PORT:-18083}/gretl-console/"
