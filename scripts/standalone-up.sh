#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
port=${GRETL_STANDALONE_PORT:-18083}
artifact=${GRETL_CONSOLE_HPI:-../gretl-jenkins-plugin/target/gretl-console.hpi}
[[ -s $artifact ]] || { echo "Build GRETL Console first or set GRETL_CONSOLE_HPI" >&2; exit 1; }
if ! docker info >/dev/null 2>&1; then
    if [[ -f $LIMA_HOME/$lab/lima.yaml ]]; then
        limactl start --tty=false "$lab"
    else
        limactl start --name="$lab" --vm-type=vz --cpus=4 --memory=8 --disk=40 \
          --tty=false --mount-none template:docker-rootful
    fi
fi
mkdir -p .local/standalone
./scripts/deploy-jobs.sh standalone
python=${GRETL_J2_PYTHON:-.local/j2-venv/bin/python}
"$python" scripts/render-casc.py standalone --label "${GRETL_AGENT_LABEL:-gretl-j4}" > .local/standalone/config.yaml
if [[ ! -s .local/standalone/users.env ]]; then
    "$python" - <<'PY' > .local/standalone/users.env
import secrets

print("chart-admin-username=admin")
print("chart-admin-password=" + secrets.token_urlsafe(24))
for user in ("operator", "reviewer", "viewer", "outsider", "dual-role"):
    print(f"jenkins-j2-users-{user}=" + secrets.token_urlsafe(24))
PY
fi
context_dir=$(mktemp -d .local/standalone-build.XXXXXX)
trap 'rm -rf "$context_dir"' EXIT
cp "$artifact" "$context_dir/gretl-console.hpi"
cp .local/standalone/{config,jobs}.yaml "$context_dir/"
chmod 644 "$context_dir"/{config,jobs}.yaml
# Use the same direct J2 plugin versions as the Helm installation, with no Kubernetes plugin.
{
    printf '%s\n' workflow-aggregator:608.v67378e9d3db_1 git:5.11.0 configuration-as-code:2147.v8a_8d38668c72
    "$python" -c 'import yaml; print(*yaml.safe_load(open("jenkins/j2-values.yaml"))["controller"]["additionalPlugins"], sep="\n")'
} > "$context_dir/plugins.txt"
docker build --provenance=false -f images/standalone.Dockerfile -t gretl-j4-controller:local "$context_dir"
name=gretl-j4-standalone
docker network inspect "$name" >/dev/null 2>&1 || docker network create "$name" >/dev/null
docker volume create "$name" >/dev/null
./scripts/standalone-down.sh
docker run -d --name "$name" --network "$name" --publish "127.0.0.1:$port:8080" \
  --env-file .local/standalone/users.env --volume "$name:/var/jenkins_home" gretl-j4-controller:local >/dev/null
if ! "$python" scripts/standalone-agent.py "$port" > .local/standalone/agent.env; then
    docker logs --tail 80 "$name" >&2
    exit 1
fi
docker run -d --name "$name-agent" --network "$name" --restart unless-stopped \
  --cap-drop ALL --security-opt no-new-privileges --env-file .local/standalone/agent.env \
  --env "JENKINS_URL=http://$name:8080/" gretl-j4-agent:local >/dev/null
expected=$(shasum -a 256 "$artifact" | cut -d' ' -f1)
actual=$(docker exec "$name" sha256sum /var/jenkins_home/plugins/gretl-console.jpi | cut -d' ' -f1)
if [[ $actual != "$expected" ]]; then
    echo "The standalone controller did not install the supplied GRETL Console HPI" >&2
    exit 1
fi
echo "GRETL Console http://127.0.0.1:$port/gretl-console/"
