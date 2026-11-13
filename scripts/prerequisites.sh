#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
[[ $(uname -s) == Darwin ]]
case "$(uname -m)" in
  arm64) arch=arm64; lima_arch=arm64; docker_arch=aarch64 ;;
  x86_64) arch=amd64; lima_arch=x86_64; docker_arch=x86_64 ;;
  *) echo "Unsupported host architecture" >&2; exit 1 ;;
esac
bin="$HOME/.local/bin"
mkdir -p "$bin"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

fetch() { curl --fail --silent --show-error --location --retry 3 -O "$1"; }
verify() { printf '%s  %s\n' "$1" "$2" | shasum -a 256 -c; }

if [[ $(limactl --version 2>/dev/null || true) != 'limactl version 2.3.0' ]]; then
  artifact=lima-2.3.0-Darwin-$lima_arch.tar.gz
  fetch "https://github.com/lima-vm/lima/releases/download/v2.3.0/$artifact"
  case $arch in
    arm64) sha=b7af444bd8f91d3c307477e562f2cf3bd7595d62b9429185e4fa841563578cfc ;;
    amd64) sha=d58bc7d6b8158d638720d8c8fdb17eb58527dfead6a29a5b60f48a35ee4a65a6 ;;
  esac
  verify "$sha" "$artifact"
  tar -tzf "$artifact" >/dev/null
  prefix="$HOME/.local/opt/lima-2.3.0"
  mkdir -p "$prefix"
  tar -xzf "$artifact" -C "$prefix"
fi

if [[ $(k3d version 2>/dev/null || true) != 'k3d version v5.9.0'* ]]; then
  artifact=k3d-darwin-$arch
  release=https://github.com/k3d-io/k3d/releases/download/v5.9.0
  fetch "$release/$artifact"
  fetch "$release/checksums.txt"
  sha=$(awk -v file="$artifact" '$2 == "_dist/" file {print $1}' checksums.txt)
  verify "$sha" "$artifact"
  install -m 755 "$artifact" "$bin/k3d"
fi

if [[ $(helm version --short 2>/dev/null || true) != v4.3.2* ]]; then
  artifact=helm-v4.3.2-darwin-$arch.tar.gz
  fetch "https://get.helm.sh/$artifact"
  fetch "https://get.helm.sh/$artifact.sha256sum"
  shasum -a 256 -c "$artifact.sha256sum"
  tar -tzf "$artifact" >/dev/null
  tar -xzf "$artifact"
  install -m 755 "darwin-$arch/helm" "$bin/helm"
fi

if ! kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; v=json.load(sys.stdin)["clientVersion"]; sys.exit(v["major"] != "1" or v["minor"] not in ("34","35","36"))'; then
  release="https://dl.k8s.io/release/v1.35.5/bin/darwin/$arch"
  fetch "$release/kubectl"
  fetch "$release/kubectl.sha256"
  verify "$(cat kubectl.sha256)" kubectl
  install -m 755 kubectl "$bin/kubectl"
fi

if ! docker --version >/dev/null 2>&1; then
  artifact=docker-29.9.3.tgz
  fetch "https://download.docker.com/mac/static/stable/$docker_arch/$artifact"
  tar -tzf "$artifact"
  tar -xzf "$artifact"
  install -m 755 docker/docker "$bin/docker"
fi

limactl --version
docker --version
k3d version
kubectl version --client
helm version --short
