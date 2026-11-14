#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$root"
platform=${1:-k3d}
dev=()
case ${GRETL_DEV_BRANCHES_ENABLED:-true} in
    true) dev=(--dev-branches) ;;
    false) ;;
    *) echo "GRETL_DEV_BRANCHES_ENABLED must be true or false" >&2; exit 2 ;;
esac
[[ $platform == k3d || $platform == standalone ]] || { echo "Expected k3d or standalone" >&2; exit 2; }
repository=${JOBS_REPOSITORY:-../gretl-runtime-jobs}
python=${GRETL_J2_PYTHON:-.local/j2-venv/bin/python}
if [[ ! -x $python ]] && [[ $python == .local/j2-venv/bin/python ]]; then
  python3 -m venv .local/j2-venv
  .local/j2-venv/bin/pip install -r scripts/j2-requirements.txt
fi
[[ -s .local/j2-image-ref ]] || ./scripts/build-j2-image.sh > .local/j2-image-ref
image=$(cat .local/j2-image-ref)
agent=$(./scripts/build-gretl-agent.sh "$image")
candidate=$(mktemp .local/j2-jobs.XXXXXX)
container=
cleanup() {
  rm -f "$candidate"
  if [[ -n $container ]]; then docker rm -f "$container" >/dev/null; fi
}
trap cleanup EXIT
revision=$(git -C "$repository" rev-parse --verify --end-of-options "${JOBS_REF:-$(cat jenkins/j2-jobs-ref)}^{commit}")
"$python" scripts/generate-jobs.py "$repository" --ref "$revision" --runtime "$image" --agent-label "${GRETL_AGENT_LABEL:-gretl-j4}" ${dev[@]+"${dev[@]}"} > "$candidate"
if ! git -C "$repository" branch -r --contains "$revision" | grep -q 'origin/'; then
  echo "Publish jobs commit $revision to origin and fetch its tracking branch first" >&2
  exit 1
fi
scripts=$("$python" scripts/generate-jobs.py "$repository" --ref "$revision" --runtime "$image" --scripts)
# Check the actual committed Groovy scripts with the same runtime used by Jenkins.
IFS=$'\n' read -r -d '' -a job_scripts <<< "$scripts" || true
container=$(docker create --network none --entrypoint /bin/bash "$image" -eu -c '
  for script do
    /opt/gretl/bin/gretl check "/tmp/catalog/$script"
  done
' check "${job_scripts[@]}")
git -C "$repository" archive --prefix=catalog/ "$revision" | docker cp - "$container:/tmp"
docker start -a "$container"
if [[ $platform == k3d ]]; then
    k3d image import "$agent" --cluster "$lab"
    ./scripts/configure-j2.sh "$candidate" "$agent"
else
    cp "$candidate" .local/standalone/jobs.yaml
fi
