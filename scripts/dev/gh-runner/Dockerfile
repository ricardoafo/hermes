# syntax=docker/dockerfile:1.7-labs
# Self-hosted GitHub Actions runner for hermes CI.
#
# Design goals: SPEED (pre-baked toolchain, warm caches, BuildKit cache mounts)
# + REPRODUCIBILITY (pinned versions of everything, ephemeral job lifecycle).
#
# Pre-baked so each job starts with tools already installed:
#   - Python 3.14 (via uv)
#   - uv itself (pinned)
#   - Node 24 (nodesource) + markdownlint-cli2 (pinned, npm -g) — the
#     markdown-lint job doesn't spend ~20s on npm install per run
#   - gitleaks binary (pinned) — the secrets job uses this directly instead
#     of pulling the gitleaks-action Docker image on every run
#   - semgrep (via uv tool) — the SAST job skips ~15-20s cold install per run
#
# BuildKit cache mounts (--mount=type=cache) persist apt + uv-install
# downloads across image rebuilds. Rebuilds after a Dockerfile edit only
# re-run the changed layers; cache mounts skip re-downloading unchanged
# packages.
#
# Runner binary version is PINNED. Self-updates during a job break the
# --ephemeral lifecycle; bump this manually + rebuild the image.

FROM ubuntu:24.04

ARG RUNNER_VERSION=2.336.0
ARG RUNNER_ARCH=x64
ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.12.5
ARG NODE_MAJOR=24
ARG MARKDOWNLINT_VERSION=0.23.2
ARG GITLEAKS_VERSION=8.21.2
ARG DOCKER_CE_CLI_VERSION=5:27.4.1-1~ubuntu.24.04~noble
ARG DEBIAN_FRONTEND=noninteractive

# ────────────────────────────────────────────────────────────────
# OCI provenance labels — visible via `docker inspect`, useful for
# audit + traceability
# ────────────────────────────────────────────────────────────────
LABEL org.opencontainers.image.source="https://github.com/ricardoafo/hermes"
LABEL org.opencontainers.image.description="Self-hosted GitHub Actions runner for hermes CI (Python 3.14 + uv + semgrep + markdownlint pre-baked)"
LABEL org.opencontainers.image.licenses="Proprietary"

# ────────────────────────────────────────────────────────────────
# Base OS + system tools
# apt cache mount avoids re-downloading .debs on rebuild.
# ────────────────────────────────────────────────────────────────
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
 && apt-get update && apt-get install -y --no-install-recommends \
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
      gnupg

# ────────────────────────────────────────────────────────────────
# Docker CLI — pinned via DOCKER_CE_CLI_VERSION for reproducibility.
# Socket-mounted from host at runtime for actions that spawn sibling
# containers.
# ────────────────────────────────────────────────────────────────
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
      gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends \
      "docker-ce-cli=${DOCKER_CE_CLI_VERSION}"

# ────────────────────────────────────────────────────────────────
# Node 24 (nodesource) — pre-baked for markdownlint-cli2 + speed on
# any Node-based action step
# ────────────────────────────────────────────────────────────────
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.npm,sharing=locked \
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g "markdownlint-cli2@${MARKDOWNLINT_VERSION}"

# ────────────────────────────────────────────────────────────────
# gitleaks (secrets scanner) — pinned binary, avoids Docker pull on
# every secrets-scan job
# ────────────────────────────────────────────────────────────────
RUN curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
      -o /tmp/gitleaks.tar.gz \
 && tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks \
 && rm /tmp/gitleaks.tar.gz \
 && chmod +x /usr/local/bin/gitleaks

# ────────────────────────────────────────────────────────────────
# Non-root runner user (GH runner refuses to run as root).
# Docker group ID 999 matches typical host docker.sock ownership.
# ────────────────────────────────────────────────────────────────
RUN useradd -m -s /bin/bash runner \
 && usermod -aG sudo runner \
 && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
 && groupadd -g 999 docker || true \
 && usermod -aG docker runner

USER runner
WORKDIR /home/runner

# Pre-create cache dir with runner ownership so uv (and later named
# volume mount) can write to it without a permission-fix step at
# entrypoint time.
RUN mkdir -p /home/runner/.cache/uv \
             /home/runner/.cache/npm \
             /home/runner/.cache/pip

# ────────────────────────────────────────────────────────────────
# uv + Python 3.14 (pre-baked as runner user so tool cache is warm
# from the first job)
# ────────────────────────────────────────────────────────────────
RUN curl -LsSf "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-installer.sh" | \
      env UV_INSTALL_DIR="/home/runner/.local/bin" sh \
 && /home/runner/.local/bin/uv python install "${PYTHON_VERSION}"

ENV PATH="/home/runner/.local/bin:${PATH}"
ENV UV_PYTHON_PREFERENCE=only-managed
ENV UV_CACHE_DIR=/home/runner/.cache/uv
ENV npm_config_cache=/home/runner/.cache/npm

# ────────────────────────────────────────────────────────────────
# semgrep (SAST) — pre-baked via uv tool. Skips ~15-20s cold install
# per CI run. Uses the same p/security-audit + p/python rulesets
# referenced in .github/workflows/ci.yml.
# ────────────────────────────────────────────────────────────────
RUN uv tool install semgrep

# ────────────────────────────────────────────────────────────────
# GH Actions runner (pinned)
# ────────────────────────────────────────────────────────────────
RUN curl -o actions-runner.tar.gz -L \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
 && tar xzf actions-runner.tar.gz \
 && rm actions-runner.tar.gz

USER root
RUN /home/runner/bin/installdependencies.sh
USER runner

COPY --chown=runner:runner entrypoint.sh /home/runner/entrypoint.sh
RUN chmod +x /home/runner/entrypoint.sh

ENTRYPOINT ["/home/runner/entrypoint.sh"]
