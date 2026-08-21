# Self-hosted GitHub Actions runner for hermes CI.
#
# Lean image: base tools only. Python + uv + Node get installed per-job via
# setup-uv / setup-node actions — keeps the runner image slim, catches
# version drift early, and avoids re-baking on every dependency bump.
#
# Runner version is PINNED. Self-updates during a job break the ephemeral
# registration lifecycle; we bump this manually + rebuild the image.

FROM ubuntu:24.04

ARG RUNNER_VERSION=2.336.0
ARG RUNNER_ARCH=x64
ARG DEBIAN_FRONTEND=noninteractive
ARG TARGETPLATFORM=linux/amd64

# Base tools + docker CLI (socket-mounted from host for actions like
# gitleaks that spin up sibling containers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    jq \
    sudo \
    unzip \
    zip \
    xz-utils \
    build-essential \
    python3 \
    python3-pip \
    apt-transport-https \
    lsb-release \
    gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
      gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli \
 && rm -rf /var/lib/apt/lists/*

# Non-root runner user (GH runner refuses to run as root)
RUN useradd -m -s /bin/bash runner \
 && usermod -aG sudo runner \
 && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner

# Docker socket group — matches host docker.sock ownership so runner can
# spawn sibling containers without root
RUN groupadd -g 999 docker || true \
 && usermod -aG docker runner

USER runner
WORKDIR /home/runner

# Runner binary (pinned)
RUN curl -o actions-runner.tar.gz -L \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
 && tar xzf actions-runner.tar.gz \
 && rm actions-runner.tar.gz

# Runner deps (installdependencies.sh needs root)
USER root
RUN /home/runner/bin/installdependencies.sh
USER runner

COPY --chown=runner:runner entrypoint.sh /home/runner/entrypoint.sh
RUN chmod +x /home/runner/entrypoint.sh

ENTRYPOINT ["/home/runner/entrypoint.sh"]
