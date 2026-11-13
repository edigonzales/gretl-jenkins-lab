#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
./scripts/build-runtime.sh
revision=$(cat .local/runtime-revision)
image=$(
  ../gretl-runtime/scripts/build-runtime-image.sh --distribution .local/dist \
    --output .local/j2-image --source "$revision" >&2
  python3 -c 'import json; print(json.load(open(".local/j2-image/candidate.json"))["buildCommand"][-2])'
)
k3d image import "$image" --cluster "$lab" >&2
docker run --rm --entrypoint java "$image" -version >&2
printf '%s\n' "$image"
