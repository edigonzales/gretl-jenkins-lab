#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
./scripts/prerequisites.sh
mkdir -p .local
if [[ -f $LIMA_HOME/$lab/lima.yaml ]]; then
  limactl start --tty=false "$lab"
else
  limactl start --name="$lab" --vm-type=vz --cpus=4 --memory=8 --disk=40 \
    --tty=false --mount-none template:docker-rootful
fi
docker info >/dev/null
if [[ $(k3d cluster list "$lab" --no-headers 2>/dev/null) == "$lab"* ]]; then
  k3d cluster start "$lab"
else
  k3d cluster create --config k3d/cluster.yaml
fi
k3d kubeconfig get "$lab" > "$KUBECONFIG.tmp"
mv "$KUBECONFIG.tmp" "$KUBECONFIG"
kube wait --for=condition=Ready node --all --timeout=90s
kube create namespace "$namespace" --dry-run=client -o yaml | kube apply -f -
kube label namespace "$namespace" app.kubernetes.io/part-of="$lab" --overwrite
./scripts/install-jenkins.sh

image=gretl-lab-tools:local
./scripts/build-runtime.sh
target=with-runtime
docker build --target "$target" -f images/tools.Dockerfile -t "$image" .
image_id=$(docker image inspect -f '{{.Id}}' "$image")
image_changed=false
if [[ $(cat .local/agent-image-id 2>/dev/null || true) != "$image_id" ]] ||
   ! docker exec "k3d-$lab-server-0" crictl inspecti "$image" >/dev/null 2>&1; then
  image_changed=true
  k3d image import "$image" --cluster "$lab"
  printf '%s\n' "$image_id" > .local/agent-image-id
fi
./scripts/open.sh > .local/setup-forward.log 2>&1 &
forward=$!
trap 'kill "$forward" 2>/dev/null || true' EXIT
for ((attempt=0; attempt<100; attempt++)); do
  port=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\).*/\1/p' .local/setup-forward.log)
  [[ -z $port ]] || break
  sleep .1
done
python3 scripts/static-agent.py "${port:?Port-forward failed}"
jenkins apply -f jenkins/static.yaml
if $image_changed; then
  jenkins rollout restart deployment/gretl-static
fi
jenkins rollout status deployment/gretl-static --timeout=180s
printf 'Jenkins: ./scripts/open.sh (or ./scripts/open.sh 18080)\n'
