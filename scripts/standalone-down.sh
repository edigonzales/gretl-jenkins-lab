#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
for name in gretl-j4-standalone-agent gretl-j4-standalone; do
    if docker container inspect "$name" >/dev/null 2>&1; then
        docker rm -f "$name" >/dev/null
    fi
done
