#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
docker info >/dev/null
kube get namespace "$namespace" >/dev/null
k3d cluster stop "$lab"
limactl stop "$lab"
