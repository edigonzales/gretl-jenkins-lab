#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
runtime=$1
docker build --provenance=false --build-arg GRETL_RUNTIME_IMAGE="$runtime" \
  -f images/gretl-agent.Dockerfile -t gretl-j4-agent:local images >&2
docker run --rm --network none --entrypoint sh gretl-j4-agent:local -ec '
    java -version 2>&1 | grep "^openjdk version \"25\."
    test "$(id -u)" -ne 0
    git --version
    timeout --kill-after=1s 2s true
    /opt/gretl/bin/gretl --version
' >&2
printf '%s\n' gretl-j4-agent:local
