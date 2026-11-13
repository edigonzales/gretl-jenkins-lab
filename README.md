# GRETL Jenkins latency lab

The lab runs on macOS with Lima VZ, Docker Engine in the guest, and one K3s
server managed by k3d. The dedicated Lima home is
`~/.local/gretl-lima`. The laboratory kubeconfig lives in
`.local/kubeconfig`. Jenkins is served through a loopback port-forward.

```sh
./scripts/prerequisites.sh
./scripts/up.sh
./scripts/open.sh 18080
```

Open http://127.0.0.1:18080. To retrieve the local admin password when needed,
run the following in a private terminal.

```sh
kubectl --kubeconfig .local/kubeconfig --context k3d-gretl-jenkins-lab \
  -n gretl-jenkins-lab exec jenkins-0 -c jenkins -- \
  cat /run/secrets/additional/chart-admin-password
```

The username is `admin`. Leave the forward running while using the browser
or collecting data.

```sh
./scripts/smoke.sh
python3 scripts/bench.py
python3 scripts/bench.py -n 1 G1 LEGACY IDLE
./scripts/down.sh
```

The runner writes CSV, summary JSON, and per-build evidence under the sibling
`gretl-jenkins-spec/measurements` directory. Dynamic runs require new pod UIDs.
Warm and permanent runs require an existing pod UID. The runner records
Jenkins and container wall-clock markers, Kubernetes timestamps with second
resolution, and elapsed host time including HTTP polling.

The regular Gradle cases use Java 11 and Gradle 7.6.4. Jenkins Remoting uses
Java 25. `--daemon` enables the Gradle daemon for a separate experiment.
The legacy case runs only the pinned and inspected `agi_dummy` build.
