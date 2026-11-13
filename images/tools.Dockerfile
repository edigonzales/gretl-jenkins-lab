FROM sogis/gretl:3.2.863@sha256:c23ffb8777636e2e1e6b0ed75e6589f6589850710af2d6ba529a3abb49d2a0b2 AS legacy
FROM eclipse-temurin:11-jdk-noble@sha256:df71d5e36d389fa00f16453c0005e8dad19eb4d7e3e4f7b156fcea61b9b46430 AS java11
FROM gradle:7.6.4-jdk11@sha256:06c99a26fc18becd96b848324f21729e30a77dc1c4ac719edd2747c78a951347 AS gradle
FROM jenkins/inbound-agent:3395.v6e5377b_0fb_3c-4-jdk25 AS tools
COPY --from=java11 /opt/java/openjdk /opt/java11
COPY --from=gradle /opt/gradle /opt/gradle
COPY --from=legacy /home/gradle/init.gradle /home/gradle/init.gradle
COPY --from=legacy /home/gradle/libs /home/gradle/libs
COPY fixtures /opt/bench
ENV GRADLE_HOME=/opt/gradle

FROM tools AS with-runtime
COPY .local/dist/ /opt/gretl/
