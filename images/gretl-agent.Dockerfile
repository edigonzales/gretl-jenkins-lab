ARG GRETL_RUNTIME_IMAGE=gretl-runtime:18eaf3254a16-8e566c1554df
FROM ${GRETL_RUNTIME_IMAGE} AS gretl
FROM jenkins/inbound-agent:3395.v6e5377b_0fb_3c-4-jdk25@sha256:b800dc3ef1e95eb74918f61d7323b31bff7fd2c8d7e692f8f56578e7541d84ea
USER root
RUN install -d -m 0775 -o 1000 -g 0 /work /work/agent
COPY --from=gretl /opt/gretl /opt/gretl
ENV HOME=/work JENKINS_AGENT_WORKDIR=/work/agent LANG=C.UTF-8 TZ=UTC
WORKDIR /work
USER 1000:0
