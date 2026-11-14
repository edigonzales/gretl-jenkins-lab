#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
candidate=$1
image=$2
python=${GRETL_J2_PYTHON:-.local/j2-venv/bin/python}
# Keep these users in the Jenkins realm and their random passwords in a cluster Secret.
if ! jenkins get secret jenkins-j2-users >/dev/null 2>&1; then
  "$python" - <<'PY' | jenkins create -f -
import json
import secrets
users = ['operator', 'reviewer', 'viewer', 'outsider', 'dual-role']
print(json.dumps(dict(apiVersion='v1', kind='Secret', metadata={'name': 'jenkins-j2-users'},
                      stringData={u: secrets.token_urlsafe(24) for u in users})))
PY
fi
"$python" scripts/render-casc.py k3d "$image" --label "${GRETL_AGENT_LABEL:-gretl-j4}" > .local/j2-casc.yaml
install_config=$(shasum -a 256 .local/j2-casc.yaml jenkins/values.yaml jenkins/j2-values.yaml scripts/install-jenkins.sh)
# The first install adds plugins. Later deployments only change the Job DSL ConfigMap.
if jenkins get cm jenkins-jenkins-config-gretl >/dev/null 2>&1 &&
   [[ $(cat .local/j2-install-config 2>/dev/null || true) == "$install_config" ]]; then
  "$python" - "$candidate" <<'PYMAP' | jenkins patch configmap jenkins-jenkins-config-gretl --type=merge --patch-file=/dev/stdin
import json
import sys
with open(sys.argv[1]) as stream:
    print(json.dumps({'data': {'gretl.yaml': stream.read()}}))
PYMAP
else
  ./scripts/install-jenkins.sh --force-conflicts --values jenkins/j2-values.yaml \
    --set-file controller.JCasC.configScripts.lab=.local/j2-casc.yaml \
    --set-file controller.JCasC.configScripts.gretl="$candidate"
  printf '%s\n' "$install_config" > .local/j2-install-config
fi
