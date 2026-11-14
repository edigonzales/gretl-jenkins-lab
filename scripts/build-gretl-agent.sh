#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
runtime=$1
docker build --provenance=false --build-arg GRETL_RUNTIME_IMAGE="$runtime" \
  -f images/gretl-agent.Dockerfile -t gretl-j4-agent:local images >&2
printf '%s\n' gretl-j4-agent:local
