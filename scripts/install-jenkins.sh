#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
kube get namespace "$namespace" >/dev/null
chart=.local/jenkins-5.12.0.tgz
if [[ ! -f $chart ]]; then
  curl -fsSL https://github.com/jenkinsci/helm-charts/releases/download/jenkins-5.12.0/jenkins-5.12.0.tgz -o "$chart.tmp"
  mv "$chart.tmp" "$chart"
fi
printf '%s  %s\n' 49cf886b3e336c5c54d57d4aa1764330c10f50a8e6caf85072b9807aa5450ec0 "$chart" | shasum -a 256 -c
{
  printf 'jobs:\n  - script: |\n'
  cat <<'DSL'
      pipelineJob('bench') {
        parameters {
          choiceParam('AGENT', ['dynamic', 'reusable', 'static', 'idle-test'])
          choiceParam('RUNTIME', ['shell', 'gradle', 'legacy', 'cli'])
          choiceParam('CHECKOUT', ['none', 'small', 'monorepo'])
          booleanParam('DAEMON', false)
        }
        definition {
          cps {
DSL
  printf "            script(new String('%s'.decodeBase64(), 'UTF-8'))\n" "$(base64 < jenkins/Jenkinsfile | tr -d '\n')"
  cat <<'DSL'
            sandbox()
          }
        }
      }
DSL
} > .local/jobs.yaml
helm upgrade --install jenkins "$chart" --kubeconfig "$KUBECONFIG" --kube-context "$context" \
  --namespace "$namespace" --values jenkins/values.yaml \
  --set-file controller.JCasC.configScripts.lab=jenkins/casc.yaml \
  --set-file controller.JCasC.configScripts.jobs=.local/jobs.yaml \
  --rollback-on-failure --wait --timeout 12m "$@"
