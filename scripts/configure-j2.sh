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
"$python" - "$image" > .local/j2-casc.yaml <<'PY'
import sys
import yaml

image = sys.argv[1]
with open('jenkins/casc.yaml') as stream:
    config = yaml.safe_load(stream)
jenkins = config['jenkins']
jenkins['authorizationStrategy'] = {'projectMatrix': {'entries': [
    {'user': {'name': '${chart-admin-username}', 'permissions': ['Overall/Administer']}},
    {'group': {'name': 'authenticated', 'permissions': ['Overall/Read']}},
]}}
for user in ['operator', 'reviewer', 'viewer', 'outsider', 'dual-role']:
    jenkins['securityRealm']['local']['users'].append(
        {'id': user, 'password': '${jenkins-j2-users-' + user + '}'})
templates = jenkins['clouds'][0]['kubernetes']['templates']
agent = dict(templates[1], name='gretl-j2', label='gretl-j2')
pod = yaml.safe_load(agent['yaml'])
pod['spec']['containers'][1]['image'] = image
agent['yaml'] = yaml.safe_dump(pod)
templates.append(agent)
yaml.safe_dump(config, sys.stdout, sort_keys=False)
PY
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
