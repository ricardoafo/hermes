#!/usr/bin/env bash
# Idempotent VPS bootstrap for hermes.
#
# Run once as root on hermes-01 (via ssh over Tailscale). Re-running is
# safe — every step reconciles missing state and skips work already done.
#
# See docs/architecture/adr-003-solution-architecture/09-deployment.md
# for the target VPS layout this script provisions.

set -euo pipefail

log()  { echo "[bootstrap] $*"; }
skip() { echo "[bootstrap] SKIP: $*"; }

# ────────────────────────────────────────────────────────────────
# 1. hermes system user (no shell login, owns /opt/hermes and /var/lib/hermes)
# ────────────────────────────────────────────────────────────────
if id hermes &>/dev/null; then
    skip "user 'hermes' already exists"
else
    log "creating hermes user (system, nologin, home=/opt/hermes)"
    useradd -r -m -d /opt/hermes -s /usr/sbin/nologin hermes
fi

# ────────────────────────────────────────────────────────────────
# 2. Directory layout (owned by hermes)
#
# Hermes install layout — enables symlink-swap rollback per D-2 of
# openspec/changes/phase-1-observability-rollback-slack-scaffolds/
# change.md (2026-08-23):
#
#   /opt/hermes/
#     versions/
#       v0.1.0/         <- installed by deploy.yml build step
#         .venv/
#         hermes-0.1.0-py3-none-any.whl
#       v0.1.1/         <- next version installed here
#       ...
#     current -> versions/v0.1.1   <- ACTIVE; systemd units point here
#     previous -> versions/v0.1.0  <- rollback target
#
# Rollback (fills in Phase 1 when `hermes engine pause` CLI exists):
#   ln -sfn /opt/hermes/previous /opt/hermes/current
#   systemctl restart hermes-*.service
#
# Idempotent — reruns preserve symlink targets if already present.
# ────────────────────────────────────────────────────────────────
for dir in /opt/hermes /opt/hermes/versions /var/lib/hermes /var/lib/hermes/backups; do
    if [[ -d "$dir" ]]; then
        skip "directory $dir already exists"
    else
        log "creating $dir"
        mkdir -p "$dir"
    fi
done
mkdir -p /etc/credstore.encrypted  # root-owned, systemd-creds target

# Placeholder file lets the symlinks point at a real path before any
# version is installed. `ln -sfn` swaps atomically; if the target
# didn't exist, some tools warn on the dangling symlink.
if [[ ! -f /opt/hermes/versions/.placeholder ]]; then
    log "creating /opt/hermes/versions/.placeholder"
    touch /opt/hermes/versions/.placeholder
fi

for link in current previous; do
    target="/opt/hermes/$link"
    if [[ -L "$target" ]]; then
        skip "symlink $target already exists -> $(readlink "$target")"
    else
        log "creating symlink $target -> versions/.placeholder (initial)"
        ln -s /opt/hermes/versions/.placeholder "$target"
    fi
done

log "reconciling ownership of /opt/hermes and /var/lib/hermes"
chown -R hermes:hermes /opt/hermes /var/lib/hermes

# ────────────────────────────────────────────────────────────────
# 3. uv (Python package manager) — TODO(phase-0): install into
#    hermes user's home, not root's; also install the pinned Python
#    version once pyproject.toml exists
# ────────────────────────────────────────────────────────────────
if command -v uv &>/dev/null; then
    skip "uv already installed: $(uv --version)"
else
    log "installing uv"
    # TODO(phase-0): install as hermes user + pin version
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# ────────────────────────────────────────────────────────────────
# 4. systemd-creds host key (encrypts secrets under /etc/credstore.encrypted)
# ────────────────────────────────────────────────────────────────
if [[ -f /var/lib/systemd/credential.secret ]]; then
    skip "systemd-creds host key already exists"
else
    log "generating systemd-creds host key (TPM2-backed)"
    systemd-creds --with-key=tpm2 setup
    log "IMPORTANT: back up /var/lib/systemd/credential.secret off-VPS."
    log "Without it, encrypted credentials are unrecoverable."
fi

# ────────────────────────────────────────────────────────────────
# 5. Tailscale (already installed by initial VPS provision; no-op)
# ────────────────────────────────────────────────────────────────
if command -v tailscale &>/dev/null; then
    skip "tailscale already installed"
else
    log "installing tailscale — 'tailscale up --ssh' still needs interactive auth"
    curl -fsSL https://tailscale.com/install.sh | sh
fi

# ────────────────────────────────────────────────────────────────
# 6. Deploy user for CI/CD (different from 'hermes' service user)
#    TODO(phase-0): create hermes-deploy user with limited sudo for
#    the systemctl restart + uv pip install commands only.
# ────────────────────────────────────────────────────────────────
# if ! id hermes-deploy &>/dev/null; then
#     log "creating hermes-deploy user"
#     useradd -r -m -d /home/hermes-deploy -s /bin/bash hermes-deploy
#     # authorized_keys populated out-of-band from GH secret VPS_SSH_KEY
#     mkdir -p /home/hermes-deploy/.ssh
#     touch /home/hermes-deploy/.ssh/authorized_keys
#     chown -R hermes-deploy:hermes-deploy /home/hermes-deploy/.ssh
#     chmod 700 /home/hermes-deploy/.ssh
#     chmod 600 /home/hermes-deploy/.ssh/authorized_keys
#     # sudo policy: restart hermes-*.service and uv pip install into /opt/hermes/.venv
#     cat > /etc/sudoers.d/hermes-deploy <<'SUDO'
# hermes-deploy ALL=(hermes) NOPASSWD: /opt/hermes/.venv/bin/uv pip install *
# hermes-deploy ALL=(root)   NOPASSWD: /bin/systemctl restart hermes-*.service
# SUDO
#     chmod 440 /etc/sudoers.d/hermes-deploy
# fi
skip "hermes-deploy user creation — TODO(phase-0), uncomment section above"

log "bootstrap complete"
log ""
log "Next steps (manual):"
log "  1. Install systemd units:  cp deploy/systemd/hermes-*.service /etc/systemd/system/"
log "                             cp deploy/systemd/hermes-*.timer /etc/systemd/system/"
log "                             systemctl daemon-reload"
log "                             systemctl enable --now hermes-health.timer"
log "  2. Provision GH secrets on the 'prod' Environment (see deploy/README.md)"
log "  3. Trigger deploy.yml with workflow_dispatch dry_run:true to verify wiring"
