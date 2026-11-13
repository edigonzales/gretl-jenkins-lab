#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
docker info --format '{{.OSType}}/{{.Architecture}}'
k3d cluster list "$lab"
kube get nodes
jenkins get pods,pvc,svc
python3 "$root/scripts/bench.py" --port "${1:-18080}" -n 1 A1 A2 B1 B2 C1 C2 G1
