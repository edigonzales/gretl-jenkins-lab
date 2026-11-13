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
# The first install adds plugins. Later deployments only change the Job DSL ConfigMap.
if jenkins get cm jenkins-jenkins-config-gretl >/dev/null 2>&1 &&
   cmp -s .local/j2-casc.yaml .local/j2-casc-applied.yaml; then
  jenkins create configmap jenkins-jenkins-config-gretl --from-file=gretl.yaml="$candidate" \
    --dry-run=client -o yaml | jenkins apply --server-side --force-conflicts -f -
  jenkins label configmap jenkins-jenkins-config-gretl jenkins-jenkins-config=true --overwrite
else
  ./scripts/install-jenkins.sh --values jenkins/j2-values.yaml \
    --set-file controller.JCasC.configScripts.lab=.local/j2-casc.yaml \
    --set-file controller.JCasC.configScripts.gretl="$candidate"
  cp .local/j2-casc.yaml .local/j2-casc-applied.yaml
fi
