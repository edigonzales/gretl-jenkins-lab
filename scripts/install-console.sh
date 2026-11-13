#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
artifact=console-plugin/target/gretl-console.hpi
[[ -s $artifact ]] || { echo "Build with mvn -f console-plugin/pom.xml verify first" >&2; exit 1; }
# The plugin resides on the existing Jenkins PVC. Publish it atomically so the
# next controller can only see a complete archive.
jenkins exec -i jenkins-0 -c jenkins -- sh -eu -c '
  cd "$JENKINS_HOME/plugins"
  cat > .gretl-console.jpi.tmp
  mv .gretl-console.jpi.tmp gretl-console.jpi
' < "$artifact"
jenkins rollout restart statefulset/jenkins
jenkins rollout status statefulset/jenkins --timeout=180s
