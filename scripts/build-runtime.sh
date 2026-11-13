#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
runtime=18eaf3254a16c121362b78406be7c01c95f152c7
volume=gretl-lab-runtime-$runtime
base=node:24-bookworm@sha256:9d266751ca3d7fdbe651c878ffd5ad0a42fed49a18c283509267c97f025b7496
java=eclipse-temurin:25-jdk-noble@sha256:80f67fb3d6f76e2d817d98a4cc2a9cf05f054d4e6adc5e2144bde671d722e63f
mkdir -p .local/dist
if [[ $(cat .local/runtime-revision 2>/dev/null || true) == "$runtime" && -x .local/dist/bin/gretl ]]; then
  exit 0
fi
docker info >/dev/null
docker build -t gretl-lab-runtime-builder:local - <<DOCKER
FROM $base AS node
FROM $java
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip git && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
DOCKER
git -C ../gretl-runtime archive "$runtime" | docker run --rm -i -e "SOURCE_REVISION=$runtime" -v "$volume:/build" \
  gretl-lab-runtime-builder:local bash -c '
    set -e
    cd /build
    tar -xf -
    ./mvnw -B -Dgretl.sourceRevision="$SOURCE_REVISION" -DskipTests -pl gretl-dist -am package
  '
docker run --rm -v "$volume:/build:ro" "$java" tar -C /build/gretl-dist/target/dist -cf - . |
  tar -xf - -C .local/dist
printf '%s\n' "$runtime" > .local/runtime-revision
