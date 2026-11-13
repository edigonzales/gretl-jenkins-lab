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

## GRETL 6 mit Jenkins (J2)

```sh
./scripts/up.sh
./scripts/deploy-jobs.sh
./scripts/open.sh 18082
```

Das Deployment liest die **committeten** V2-Definitionen des
Schwester-Checkouts `gretl-runtime-jobs`. Die Revision muss über dessen
GitHub-Origin erreichbar sein. Standard ist `jenkins/j2-jobs-ref`.
`JOBS_REF` überschreibt die Vorgabe für ein administratives Deployment.
`JOBS_REPOSITORY` wählt den Checkout. Jenkins verwendet während des gesamten
Builds denselben Git-SHA. Das erste Deployment installiert die zusätzlichen
Plugins. Folgende Katalog-Deployments aktualisieren die Job-DSL-ConfigMap
und Jenkins übernimmt sie über JCasC.

Anmeldung als `operator` zum Start, als `reviewer` für Freigaben und als
`viewer` zum Lesen. Die lokalen Testzugänge liegen im Kubernetes-Secret.
Die Gruppenregeln des Katalogs werden für das Labor zusätzlich auf diese
Benutzer abgebildet. Jenkins-Administratoren behalten ihre nativen
Jenkins-Rechte. Die Pipeline blockiert Selbstfreigaben nach dem Absenden.

```sh
source scripts/env.sh
jenkins get secret jenkins-j2-users -o jsonpath='{.data.operator}' | base64 -d
```

Öffne `gretl-csv-publication`, wähle **Build with Parameters** und lade
`gretl-runtime-jobs/testdata/publication.csv` hoch. Der Prüfbericht steht im
Build unter `prepare/review.json`. Als Reviewer **Paused for Input** öffnen,
den Publikationshinweis erfassen und **Proceed** wählen. Die Dateien unter
`publish/` lassen sich über **Build Artifacts** herunterladen.

```sh
.local/j2-venv/bin/python scripts/test-generate-jobs.py
python3 scripts/j2-e2e.py --port 18082 --restart --examples --large \
  --evidence ../gretl-jenkins-spec/evidence/run
python3 -m venv .local/j2-browser
.local/j2-browser/bin/pip install playwright
.local/j2-browser/bin/playwright install chromium
.local/j2-browser/bin/python scripts/j2-browser.py --port 18082 \
  --evidence ../gretl-jenkins-spec/evidence/browser
./scripts/down.sh
```

Für `--restart` muss der lokale Port-Forward nach dem Podwechsel erneut
gestartet werden. Er kann für den Test in einer Schleife laufen.
Agenten bleiben nach einem fachlichen Schritt bis zu 15 Minuten wiederverwendbar.
J2 verwendet ein gemeinsames CPU- und RAM-Profil für den wiederverwendbaren
Agenten. Die V2-Ressourcenangaben werden von der Kubernetes Job Console
ausgewertet. HTTP-Clients verwenden die generierten Jenkins-Namen mit
Präfix `J2_`, etwa `J2_inputFile`.
Jenkins hält während eines Input-Schritts keinen Executor. Serielle Flows
behalten ihren Platz bis zum Abschluss inklusive Freigabe. Stashes liegen im
Jenkins-Home. Der Laufbericht des generischen Runtime-Image-Builders steht in
`.local/j2-image/candidate.json`. Die Pipeline serialisiert validierte Werte
als Strings, wie es die gepinnte GRETL-CLI für `--params-file` verlangt.

Die ergänzende Abnahme prüft ungültige Kataloge, die Fehlerpfade der
gemeinsamen Pipeline und einen Katalogwechsel während der Freigabe.
Sie legt kurzzeitig synthetische Jenkins-Jobs an und stellt den
ursprünglichen Katalog danach wieder her. Führe die Abnahmeskripte
nacheinander auf dem dedizierten Labor aus.

```sh
.local/j2-venv/bin/python scripts/j2-acceptance.py --port 18082 \
  --evidence ../gretl-jenkins-spec/evidence/acceptance-extra
```

## GRETL Console (J3)

Die Console wird als Jenkins-Plugin im vorhandenen Labor installiert.

```sh
mvn -f console-plugin/pom.xml verify
./scripts/deploy-jobs.sh
./scripts/install-console.sh
```

Öffne [GRETL Console](http://127.0.0.1:18082/gretl-console/) im Labor.
Die Console zeigt für die aktuelle Jenkins-Anmeldung lesbare GRETL-Jobs.
Operatoren können sie aus dem Formular starten. Die Eingabemaske verwendet
Datumsauswahl, numerische Felder und die fachlichen Auswahlbezeichnungen aus
Schema V2. Die Historie verlinkt die nativen Buildlogs, Artefakte und offenen
Freigaben. Die Rollen und Zugangsdaten entsprechen J2.

Der Generator schreibt die Anzeigemetadaten beim Deployment in die
Jobdefinition. Die Formulare senden direkt an den nativen Jenkins-Build-Endpunkt.
Dateien verarbeitet das File Parameter Plugin. Zeitpläne, Queue, Revision und
Freigaben folgen der bereitgestellten J2-Konfiguration. Der Git-Link zeigt den
bereitgestellten Commit. Ein anderer Ref wird durch den administrativen
`JOBS_REF`-Deploymentpfad ausgewählt.

`install-console.sh` ersetzt das Plugin auf dem bestehenden PVC und startet
den Jenkins-Controller neu. Das Browser-Skript nutzt die Playwright-Umgebung
aus J2.

```sh
.local/j2-browser/bin/python scripts/j3-browser.py --port 18082 \
  --evidence ../gretl-jenkins-spec/evidence/j3/browser
```
