FROM jenkins/jenkins:2.580.2-jdk25@sha256:415ab90d4e3b25148f7191bc04f455a20817d6c4c3b0a98f6c6b0da4877f7081
COPY --chmod=0644 plugins.txt /tmp/gretl-plugins.txt
RUN jenkins-plugin-cli --plugin-file /tmp/gretl-plugins.txt
COPY --chmod=0644 gretl-console.hpi /usr/share/jenkins/ref/plugins/gretl-console.jpi.override
USER root
RUN install -d -m 0755 /usr/share/gretl-casc
COPY --chmod=0644 config.yaml /usr/share/gretl-casc/config.yaml
COPY --chmod=0644 jobs.yaml /usr/share/gretl-casc/jobs.yaml
USER jenkins
ENV CASC_JENKINS_CONFIG=/usr/share/gretl-casc JAVA_OPTS=-Djenkins.install.runSetupWizard=false
