#!/bin/bash
export PATH="$HOME/.local/opt/lima-2.3.0/bin:$HOME/.local/bin:$PATH"
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export LIMA_HOME="$HOME/.local/gretl-lima"
lab=gretl-jenkins-lab
namespace=$lab
context=k3d-$lab
export KUBECONFIG="$root/.local/kubeconfig"
export DOCKER_HOST="unix://$LIMA_HOME/$lab/sock/docker.sock"
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
umask 077

kube() {
  if [[ $(kubectl --kubeconfig "$KUBECONFIG" config current-context) != "$context" ]]; then
    echo "Unexpected laboratory Kubernetes context" >&2
    return 1
  fi
  kubectl --kubeconfig "$KUBECONFIG" --context "$context" "$@"
}

jenkins() {
  kube -n "$namespace" "$@"
}
