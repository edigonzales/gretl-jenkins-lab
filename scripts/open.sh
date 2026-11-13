#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
kube get namespace "$namespace" >/dev/null
exec kubectl --kubeconfig "$KUBECONFIG" --context "$context" -n "$namespace" \
  port-forward --address 127.0.0.1 svc/jenkins "${1:-}:8080"
