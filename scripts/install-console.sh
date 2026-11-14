#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
artifact=${1:-"$root/../gretl-jenkins-plugin/target/gretl-console.hpi"}
cd "$root"
[[ -s $artifact ]] || { echo "Run Maven verify in gretl-jenkins-plugin or pass the built HPI" >&2; exit 1; }
# The plugin resides on the existing Jenkins PVC. Publish it atomically so the
# next controller can only see a complete archive.
jenkins exec -i jenkins-0 -c jenkins -- sh -eu -c '
  cd "$JENKINS_HOME/plugins"
  cat > .gretl-console.jpi.tmp
  mv .gretl-console.jpi.tmp gretl-console.jpi
' < "$artifact"
jenkins rollout restart statefulset/jenkins
jenkins rollout status statefulset/jenkins --timeout=360s
expected=$(shasum -a 256 "$artifact" | cut -d' ' -f1)
actual=$(jenkins exec jenkins-0 -c jenkins -- sha256sum /var/jenkins_home/plugins/gretl-console.jpi | cut -d' ' -f1)
if [[ $actual != "$expected" ]]; then
  echo "The installed GRETL Console archive differs from the supplied HPI" >&2
  exit 1
fi
