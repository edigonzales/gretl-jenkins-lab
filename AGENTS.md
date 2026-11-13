# GRETL Jenkins lab

Use only the dedicated Lima home and the k3d cluster selected by scripts/env.sh.
Run only the supplied fixtures and the inspected agi_dummy build.
Keep credentials and generated configuration under .local or in Kubernetes Secrets.
Run the smoke test after changing Jenkins or agent configuration. Keep measurements
and review reports in the sibling gretl-jenkins-spec directory.
