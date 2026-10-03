#!/usr/bin/env bash
# =====================================================================
# VPS bootstrap — idempotent post-provisioning setup for hermes-01
# =====================================================================
#
# Runs on the VPS via `ssh bash < scripts/deploy/vps_bootstrap.sh`
# (invoked by .github/workflows/vps-bootstrap.yml). Consumes HERMES_*
# and STORAGE_BOX_* env vars that the workflow propagates from the
# `prod` GH environment.
#
# Idempotent — every step is `install -d ... || true` OR a diffing
# check before writing / mounting. Safe to re-run any number of times.
#
# Steps:
#   1. Ensure FHS directories (/var/log/hermes, /etc/hermes).
#   2. Render /etc/hermes/env.production from HERMES_* env vars
#      (atomic tmp + mv; 0400 hermes:hermes).
#   3. Mount Hetzner Storage Box via SSHFS (guarded — skipped if
#      credentials absent).
#
# Reference:
#   openspec/changes/chore-vps-bootstrap-workflow/change.md
#   .github/workflows/vps-bootstrap.yml

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

echo "==== VPS bootstrap starting on $(hostname) ===="
# `id -un` may be masked in workflow logs when the username happens to
# equal a passed-through secret (e.g. VPS_SSH_USER). Print uid + groups
# alongside so the identity is legible regardless. Also probe sudo
# availability — the script needs root to write /var/log/hermes and
# /etc/hermes; if SSH mapped us to a non-root user (Tailscale-SSH ACL
# gotcha, see #290 follow-up), we need passwordless sudo to elevate.
echo "user:   uid=$(id -u) gid=$(id -g)  ($(id -un))"
echo "groups: $(id -Gn)"
if [ "$(id -u)" = "0" ]; then
    echo "sudo:   n/a (already root)"
elif sudo -n true 2>/dev/null; then
    echo "sudo:   passwordless OK"
else
    echo "sudo:   NOT available — install-d ops will fail unless you're root"
fi
echo "date:   $(date -u -Iseconds)"
echo ""

# ────────────────────────────────────────────────────────────────
# 1. Ensure FHS directories
# ────────────────────────────────────────────────────────────────
echo "---- 1. FHS directories ----"
for dir in /var/log/hermes /etc/hermes; do
    if [ -d "$dir" ]; then
        echo "  $dir exists — leaving as-is"
    else
        install -d -m 0750 -o hermes -g hermes "$dir"
        echo "  $dir created (hermes:hermes 0750)"
    fi
done
echo ""

# ────────────────────────────────────────────────────────────────
# 2. Render /etc/hermes/env.production from HERMES_* env vars
#
# Approach: iterate all env vars matching HERMES_*, write each as
# KEY=VALUE. Skip empty-valued vars (avoids writing "KEY=" lines that
# systemd interprets as "unset the var"). Use tmp file + atomic mv so
# a mid-write failure leaves the previous file intact.
# ────────────────────────────────────────────────────────────────
echo "---- 2. Render /etc/hermes/env.production ----"

# umask 0377 = created files are 0400.
umask 0377

tmp="/etc/hermes/env.production.tmp"
: > "$tmp"

written=0
skipped_empty=0
for var in $(env | grep -oE '^HERMES_[A-Z][A-Z0-9_]*' | sort -u); do
    # Indirect expansion — ${!var} is POSIX-ok in bash.
    val="${!var}"
    if [ -z "$val" ]; then
        skipped_empty=$((skipped_empty + 1))
        continue
    fi
    # Escape backslashes and doublequotes; wrap in doublequotes so systemd
    # EnvironmentFile handles values with spaces / special chars correctly.
    escaped=$(printf '%s' "$val" | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '%s="%s"\n' "$var" "$escaped" >> "$tmp"
    written=$((written + 1))
done

chown hermes:hermes "$tmp"
chmod 0400 "$tmp"
mv "$tmp" /etc/hermes/env.production

echo "  wrote /etc/hermes/env.production"
echo "  vars written: $written  (skipped-empty: $skipped_empty)"
echo "  perm: $(stat -c '%a %U:%G' /etc/hermes/env.production)"
echo ""

# ────────────────────────────────────────────────────────────────
# 3. Storage-box mount (guarded)
# ────────────────────────────────────────────────────────────────
echo "---- 3. Storage-box mount ----"

if [ -z "${STORAGE_BOX_HOST:-}" ] || [ -z "${STORAGE_BOX_USER:-}" ] || [ -z "${STORAGE_BOX_KEY:-}" ]; then
    echo "  WARN: storage-box credentials missing — skipping mount"
    echo "  (add STORAGE_BOX_HOST + STORAGE_BOX_USER + STORAGE_BOX_KEY to the prod env when ready)"
else
    # Ensure mount point + sshfs binary. On a re-run the sshfs mount is live
    # and FUSE rejects chown/chmod on its root, so only prepare it unmounted.
    if mountpoint -q /mnt/storage-box; then
        echo "  /mnt/storage-box already mounted — leaving mount point as is"
    else
        install -d -m 0755 -o hermes -g hermes /mnt/storage-box
    fi
    if ! command -v sshfs >/dev/null 2>&1; then
        echo "  installing sshfs..."
        apt-get update -qq && apt-get install -y sshfs
    fi

    # Storage-box key at a stable location, 0400 hermes:hermes
    key_path=/home/hermes/.ssh/storage_box_ed25519
    install -d -m 0700 -o hermes -g hermes /home/hermes/.ssh
    umask 0377
    printf '%s\n' "$STORAGE_BOX_KEY" > "$key_path.tmp"
    chown hermes:hermes "$key_path.tmp"
    chmod 0400 "$key_path.tmp"
    mv "$key_path.tmp" "$key_path"
    echo "  wrote storage-box key to $key_path"

    # Systemd auto-mount unit (survives reboot). If it exists and matches
    # the current credentials, leave it. Otherwise write + reload.
    unit_path='/etc/systemd/system/mnt-storage\x2dbox.mount'
    expected_what="$STORAGE_BOX_USER@$STORAGE_BOX_HOST:/"

    if [ -f "$unit_path" ] && grep -qF "What=$expected_what" "$unit_path"; then
        echo "  mount unit already exists and matches — no change"
    else
        cat > "$unit_path" <<EOF
[Unit]
Description=Hetzner Storage Box (SSHFS)
Requires=network-online.target
After=network-online.target

[Mount]
What=$expected_what
Where=/mnt/storage-box
Type=fuse.sshfs
Options=_netdev,IdentityFile=$key_path,port=23,uid=$(id -u hermes),gid=$(id -g hermes),allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,StrictHostKeyChecking=accept-new

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        echo "  wrote $unit_path"
    fi

    if ! systemctl is-enabled --quiet 'mnt-storage\x2dbox.mount'; then
        systemctl enable 'mnt-storage\x2dbox.mount'
        echo "  enabled mount unit"
    fi

    if ! mountpoint -q /mnt/storage-box; then
        systemctl start 'mnt-storage\x2dbox.mount' || {
            echo "  ERROR: mount failed — inspect: journalctl -xeu mnt-storage\\x2dbox.mount"
            exit 1
        }
    fi

    # Verify
    if mountpoint -q /mnt/storage-box; then
        echo "  MOUNTED: $(df -h /mnt/storage-box | tail -1)"
    else
        echo "  ERROR: storage-box still not mounted after start"
        exit 1
    fi

    # Ensure the shadow-mode subdir exists
    sudo -u hermes install -d -m 0755 /mnt/storage-box/shadow-mode 2>/dev/null || true
fi
echo ""

echo "==== VPS bootstrap complete ===="
