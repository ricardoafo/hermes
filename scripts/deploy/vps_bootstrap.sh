#!/usr/bin/env bash
# =====================================================================
# VPS bootstrap — idempotent post-provisioning setup for hermes-01
# =====================================================================
#
# Runs on the VPS as root via `ssh bash < scripts/deploy/vps_bootstrap.sh`
# (invoked by .github/workflows/vps-bootstrap.yml). Consumes HERMES_*
# and STORAGE_BOX_* env vars that the workflow propagates from the
# `prod` GH environment. The script arrives on stdin, so it cannot find
# files next to itself: the workflow ships deploy/systemd/ and the two
# alert scripts (deploy/hermes-notify-failure.sh, deploy/hermes-notify-crash.sh)
# to /var/tmp/hermes-bootstrap/ in an earlier step.
#
# Safe to re-run any number of times. Every step checks state before it
# acts; the hazards a re-run can hit are named where they apply:
#   - chown on a live FUSE mount fails with EPERM even as root (step 6).
#   - a leaked `umask 0377` makes later files unreadable (set once, below).
#   - re-rendering the env file would overwrite hand edits (step 5).
#
# Steps:
#   1. hermes system user.
#   2. FHS directories: code is root-owned, state is hermes-owned.
#   3. uv (pinned to the CI version) and a pinned Python 3.14.x under
#      /opt/hermes/python, both verified after the install (hermes must be
#      able to run the interpreter).
#   4. systemd units copied to /etc/systemd/system; hermes.service enabled
#      (not started: the first deploy starts it). The backup timer stays
#      off until borg is set up. The OnFailure= alert script goes to
#      /opt/hermes/bin, so it exists before any release does, and a stale
#      failed alert unit is reset (#364).
#   5. /etc/hermes/env.production: rendered from HERMES_* vars only when the
#      file is absent; when present only owner and mode are repaired. The
#      health timer is enabled once the file has a healthchecks.io ping URL.
#   6. Hetzner Storage Box SSHFS mount (guarded: skipped without credentials).
#   7. sudoers rule for a non-root CD deploy user (guarded: skipped unless
#      DEPLOY_USER names one; CD runs as root today, so nothing is written).
#   8. Unattended security upgrades with an automatic reboot at 03:30 box
#      time (Europe/Madrid) when an update needs one: clear of the 00:00
#      and 04:00 UTC bar closes and the 03:00 UTC backup in both summer
#      and winter time.
#   9. journald drop-in with ADR-008 §1 limits (2G, 180 days); journald is
#      restarted only when the drop-in changed.
#
# Reference:
#   openspec/changes/179-cd-first-real-deploy/change.md (D-5, D-8, R-5)
#   .github/workflows/vps-bootstrap.yml

set -euo pipefail

# Never let secrets leak via `set -x` even if something above turned it on.
set +x

# Fixed once, so every file below gets 0644/0755 unless a step says otherwise.
umask 022

# Same pin as `astral-sh/setup-uv` in .github/workflows/cd.yml; a test keeps
# the two equal.
UV_VERSION=0.12.5
BOOTSTRAP_DIR=/var/tmp/hermes-bootstrap
SYSTEMD_SRC="$BOOTSTRAP_DIR/systemd"
# Alert scripts the units run from /opt/hermes/bin, outside any release.
NOTIFIERS=(hermes-notify-failure.sh hermes-notify-crash.sh)
ENV_FILE=/etc/hermes/env.production
PYTHON_DIR=/opt/hermes/python
# Exact interpreter the release venvs are built on (pyproject requires
# >=3.14,<3.15). A bump is a one-line change here, then a re-dispatch.
PYTHON_VERSION=3.14.7
# Optional: the non-root account CD connects as. Unset or root: no sudoers rule.
DEPLOY_USER=${DEPLOY_USER:-}
SUDOERS_FILE=/etc/sudoers.d/hermes-deploy
AUTO_REBOOT_FILE=/etc/apt/apt.conf.d/52hermes-auto-reboot
# The reboot time is read in the box's own timezone; 03:30 is chosen for
# Europe/Madrid, so the bootstrap refuses to arm it on any other zone.
AUTO_REBOOT_TZ=Europe/Madrid
# journald limits from ADR-008 §1 (#512).
JOURNALD_DROPIN=/etc/systemd/journald.conf.d/50-hermes.conf

# uv installs to /usr/local/bin; a non-interactive ssh PATH may omit it.
export PATH="/usr/local/bin:$PATH"

# Hidden temp file for the env render, removed on any exit.
env_tmp=""
cleanup() {
    if [ -n "$env_tmp" ]; then
        rm -f "$env_tmp"
    fi
}
trap cleanup EXIT

# Render the env file only when it is absent; otherwise repair owner and mode
# and leave the content alone. Re-rendering on every run would overwrite hand
# fixes, and would write an empty file when no HERMES_* var is passed. To
# rotate a secret, edit the file, or delete it and re-dispatch.
# Extracted by tests/integration/deploy/test_vps_bootstrap.py: keep it
# self-contained (it may use env_tmp and the HERMES_* environment only).
ensure_env_file() {
    local file=$1 var val escaped written=0 skipped_empty=0
    if [ -f "$file" ]; then
        echo "  exists — content left untouched, repairing owner and mode only"
        chown root:root "$file"
        chmod 0600 "$file"
    else
        # mktemp creates the file 0600, so the secrets are never readable by others.
        env_tmp=$(mktemp "$(dirname "$file")/.env.production.XXXXXX")
        # Every HERMES_* var, skipping empty ones: a "KEY=" line would make systemd
        # unset the variable instead of leaving it alone.
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
            printf '%s="%s"\n' "$var" "$escaped" >> "$env_tmp"
            written=$((written + 1))
        done
        echo "  vars written: $written  (skipped-empty: $skipped_empty)"
        if [ "$written" -eq 0 ]; then
            echo "  WARN: no HERMES_* var set — not creating an empty env file."
            echo "  Create it by hand: install -m 0600 -o root -g root /dev/null $file"
            echo "  then add HERMES_ENV=prod and HERMES_ALLOW_MAINNET=no."
            rm -f "$env_tmp"
        else
            # The unit reads the file as root (systemd does, before dropping to
            # User=hermes), so hermes never needs access.
            chown root:root "$env_tmp"
            chmod 0600 "$env_tmp"
            mv -T "$env_tmp" "$file"
            echo "  wrote $file"
        fi
        env_tmp=""
    fi
    if [ -f "$file" ]; then
        echo "  perm: $(stat -c '%a %U:%G' "$file")"
    fi
}

# Prepare a mount point only while nothing is mounted on it: chown on a live
# SSHFS mount fails with EPERM even as root, and a mounted point was already
# prepared. Run 37070991389 failed on a re-dispatch because `install -d -o`
# ran unconditionally. Extracted by tests/integration/deploy/test_vps_bootstrap.py.
prepare_mount_point() {
    local dir=$1
    if mountpoint -q "$dir"; then
        echo "  $dir already mounted — not touching the mount point"
    else
        install -d -m 0755 -o hermes -g hermes "$dir"
    fi
}

# Fail once, naming every missing tool, before any step changes the box.
require_tools() {
    local tool missing=()
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "ERROR: missing tools: ${missing[*]} (apt-get install them, then re-dispatch)"
        return 1
    fi
}

# Install uv $1 to /usr/local/bin unless that exact version is already there,
# then verify. The installer URL pins the version, not the content: root-only,
# TLS to the vendor.
ensure_uv() {
    local want=$1 have=""
    if command -v uv >/dev/null 2>&1; then
        have=$(uv --version | awk '{print $2}')
    fi
    if [ "$have" = "$want" ]; then
        echo "  uv $want already installed"
        return 0
    fi
    echo "  installing uv $want to /usr/local/bin (found: ${have:-none})"
    curl -LsSf "https://astral.sh/uv/$want/install.sh" \
        | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
    hash -r
    have=$(uv --version | awk '{print $2}')
    if [ "$have" != "$want" ]; then
        echo "ERROR: uv reports '$have' after the install, expected $want"
        return 1
    fi
}

# Install Python $1 under $PYTHON_DIR unless that exact version is already
# there, then verify it is where hermes.service can read it and that the
# hermes user can run it. The default uv dir is under /root, which the unit
# cannot read (ProtectHome=true), so a venv built on it would fail with 203/EXEC.
ensure_python() {
    local version=$1 found="" reported
    if found=$(uv python find "$version" < /dev/null 2>/dev/null) && [[ "$found" == "$PYTHON_DIR"/* ]]; then
        echo "  python $version already installed: $found"
    else
        echo "  installing python $version under $PYTHON_DIR"
        # stdin is the script itself; a child that reads it would eat the rest.
        uv python install "$version" < /dev/null
        found=$(uv python find "$version" < /dev/null)
    fi
    if [[ "$found" != "$PYTHON_DIR"/* ]]; then
        echo "ERROR: python $version resolves to $found, outside $PYTHON_DIR"
        return 1
    fi
    reported=$(runuser -u hermes -- "$found" --version < /dev/null)
    if [ "$reported" != "Python $version" ]; then
        echo "ERROR: hermes runs $found as '$reported', expected 'Python $version'"
        return 1
    fi
    echo "  verified: user hermes runs $found ($reported)"
}

# Copy every unit file from $1 into $2 when its content differs; report
# through `units_changed` so the caller reloads systemd once.
units_changed=0
install_units() {
    local src_dir=$1 dst_dir=$2 src dst
    for src in "$src_dir"/*.service "$src_dir"/*.timer; do
        [ -f "$src" ] || continue
        dst="$dst_dir/$(basename "$src")"
        if cmp -s "$src" "$dst"; then
            echo "  $(basename "$src"): unchanged"
        else
            install -m 0644 -o root -g root "$src" "$dst"
            units_changed=1
            echo "  $(basename "$src"): installed"
        fi
    done
}

# Copy the alert script $1 to $2 (0755 root:root) when its content differs.
# hermes-notify-failure@.service and hermes.service's ExecStopPost= run them
# from there, not from the release, so an outage caused by a missing or
# broken release still alerts (#364, #380). install_release.sh refreshes
# them after each good deploy.
install_notifier() {
    local src=$1 dst=$2
    if [ ! -f "$src" ]; then
        echo "  ERROR: $src missing — vps-bootstrap.yml must ship deploy/$(basename "$src")"
        return 1
    fi
    if cmp -s "$src" "$dst"; then
        echo "  $(basename "$dst"): unchanged"
    else
        install -m 0755 -o root -g root "$src" "$dst"
        echo "  $(basename "$dst"): installed"
    fi
}

# Enable and start hermes-health.timer when env file $1 carries a non-empty
# HERMES_HEALTHCHECKS__PING_URL; without one every ping would fail, so the
# timer stays off. `enable --now` is a no-op on an already running timer.
# The ping script only pings while hermes.service is active, so turning the
# timer on before the first deploy sends nothing.
ensure_health_timer() {
    local file=$1
    if [ -f "$file" ] && grep -qE '^HERMES_HEALTHCHECKS__PING_URL="?[^"[:space:]]' "$file"; then
        systemctl enable --now hermes-health.timer
        echo "  hermes-health.timer: enabled and started"
    else
        echo "  hermes-health.timer: left off (HERMES_HEALTHCHECKS__PING_URL not set in $file)"
    fi
}

# Enable and start hermes-daily.timer when env file $1 carries a non-empty
# HERMES_SLACK__BOT_TOKEN; without one every daily line would fail, so the
# timer stays off (#373). `enable --now` is a no-op on a running timer.
ensure_daily_timer() {
    local file=$1
    if [ -f "$file" ] && grep -qE '^HERMES_SLACK__BOT_TOKEN="?[^"[:space:]]' "$file"; then
        systemctl enable --now hermes-daily.timer
        echo "  hermes-daily.timer: enabled and started"
    else
        echo "  hermes-daily.timer: left off (HERMES_SLACK__BOT_TOKEN not set in $file)"
    fi
}

# The sudoers file for a non-root CD user: exactly the commands the deploy
# step runs as root (cd.yml), nothing else. The 12 hex classes match the
# sha12 release id; sudoers wildcards are fnmatch patterns.
render_deploy_sudoers() {
    local user=$1 hex='[0-9a-f]' sha
    sha=$hex$hex$hex$hex$hex$hex$hex$hex$hex$hex$hex$hex
    cat <<SUDOERS
# Managed by scripts/deploy/vps_bootstrap.sh; edits are overwritten on the next run.
# CD deploy (cd.yml) and manual rollback, as root, for $user only.
$user ALL=(root) NOPASSWD: /usr/bin/bash /var/tmp/hermes-release-$sha/bin/install_release.sh $sha /var/tmp/hermes-release-$sha
$user ALL=(root) NOPASSWD: /opt/hermes/bin/rollback.sh
SUDOERS
}

# Write $SUDOERS_FILE for user $1 (0440 root:root) when absent or different.
# The candidate is a dot-file in /etc/sudoers.d, which sudo ignores, and only
# moves into place after `visudo -cf` accepts it: a bad rule never goes live
# and never locks sudo out.
ensure_deploy_sudoers() {
    local user=${1:-} tmp
    if [ -z "$user" ] || [ "$user" = root ]; then
        echo "  no non-root deploy user (DEPLOY_USER unset or root): no sudoers rule needed"
        return 0
    fi
    if ! [[ "$user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
        echo "ERROR: DEPLOY_USER '$user' is not a plain login name"
        return 1
    fi
    if ! id "$user" >/dev/null 2>&1; then
        echo "ERROR: deploy user $user does not exist on this box"
        return 1
    fi
    if [ -f "$SUDOERS_FILE" ] && [ "$(render_deploy_sudoers "$user")" = "$(< "$SUDOERS_FILE")" ]; then
        echo "  $SUDOERS_FILE already current"
        chown root:root "$SUDOERS_FILE"
        chmod 0440 "$SUDOERS_FILE"
        return 0
    fi
    tmp=$(mktemp "$(dirname "$SUDOERS_FILE")/.hermes-deploy.XXXXXX")
    render_deploy_sudoers "$user" > "$tmp"
    chown root:root "$tmp"
    chmod 0440 "$tmp"
    if ! visudo -cf "$tmp" >/dev/null; then
        rm -f "$tmp"
        echo "ERROR: visudo rejected the rule for $user; $SUDOERS_FILE not changed"
        return 1
    fi
    mv -T "$tmp" "$SUDOERS_FILE"
    echo "  wrote $SUDOERS_FILE for $user ($(stat -c '%a %U:%G' "$SUDOERS_FILE"))"
}

# apt settings for unattended security upgrades plus the reboot they may
# need. Rendered, not hand-written, so a re-run can compare byte for byte.
render_auto_reboot() {
    cat <<'EOF'
// Managed by scripts/deploy/vps_bootstrap.sh (step 8). Edits are overwritten.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:30";
EOF
}

# Install unattended-upgrades if missing, write the reboot policy only when
# it differs, and enable the service. Refuses on an unexpected timezone:
# 03:30 is only clear of bar closes and the backup in Europe/Madrid.
ensure_auto_reboot() {
    local tz tmp
    tz=$(timedatectl show -p Timezone --value 2>/dev/null || true)
    if [ "$tz" != "$AUTO_REBOOT_TZ" ]; then
        echo "ERROR: box timezone is '${tz:-unknown}', expected $AUTO_REBOOT_TZ; reboot time not armed"
        return 1
    fi
    if ! dpkg-query -W -f='${Status}' unattended-upgrades 2>/dev/null | grep -q 'install ok installed'; then
        echo "  installing unattended-upgrades..."
        apt-get update -qq < /dev/null && apt-get install -y unattended-upgrades < /dev/null
    fi
    if [ -f "$AUTO_REBOOT_FILE" ] && [ "$(render_auto_reboot)" = "$(< "$AUTO_REBOOT_FILE")" ]; then
        echo "  $AUTO_REBOOT_FILE already current"
    else
        tmp=$(mktemp "$(dirname "$AUTO_REBOOT_FILE")/.52hermes-auto-reboot.XXXXXX")
        render_auto_reboot > "$tmp"
        chmod 0644 "$tmp"
        mv -T "$tmp" "$AUTO_REBOOT_FILE"
        echo "  wrote $AUTO_REBOOT_FILE (reboot at 03:30 $tz when an update needs it)"
    fi
    if systemctl is-enabled --quiet unattended-upgrades.service; then
        echo "  unattended-upgrades.service already enabled"
    else
        systemctl enable --now unattended-upgrades.service
    fi
}

# The journald drop-in: ADR-008 §1 caps the journal at 2G and 180 days (#512).
render_journald_dropin() {
    cat <<'EOF'
# Written by hermes vps-bootstrap (#512). ADR-008 §1: journald is the log
# store of record; keep at most 2G and at most 180 days.
[Journal]
SystemMaxUse=2G
MaxRetentionSec=180day
EOF
}

# Write the drop-in only when it differs and restart journald once so the
# new limits apply; a re-run on a current box changes nothing.
ensure_journald_retention() {
    local tmp dir
    dir=$(dirname "$JOURNALD_DROPIN")
    if [ -f "$JOURNALD_DROPIN" ] && [ "$(render_journald_dropin)" = "$(< "$JOURNALD_DROPIN")" ]; then
        echo "  $JOURNALD_DROPIN already current"
        return 0
    fi
    mkdir -p "$dir"
    tmp=$(mktemp "$dir/.50-hermes.XXXXXX")
    render_journald_dropin > "$tmp"
    chmod 0644 "$tmp"
    mv -T "$tmp" "$JOURNALD_DROPIN"
    systemctl restart systemd-journald
    echo "  wrote $JOURNALD_DROPIN (SystemMaxUse=2G, MaxRetentionSec=180day) and restarted journald"
}

echo "==== VPS bootstrap starting on $(hostname) ===="
# `id -un` may be masked in workflow logs when the username happens to
# equal a passed-through secret (e.g. VPS_SSH_USER). Print uid + groups
# alongside so the identity is legible regardless.
echo "user:   uid=$(id -u) gid=$(id -g)  ($(id -un))"
echo "groups: $(id -Gn)"
echo "date:   $(date -u -Iseconds)"
echo ""

if [ "$(id -u)" != "0" ]; then
    # Every step below writes under /opt, /etc or /var and none uses sudo.
    echo "ERROR: bootstrap must run as root (VPS_SSH_USER is not root)"
    exit 1
fi
# Checked up front so a missing tool fails before any step changes the box.
require_tools curl tar cmp install mountpoint runuser systemctl journalctl
echo ""

# ────────────────────────────────────────────────────────────────
# 1. hermes system user
# ────────────────────────────────────────────────────────────────
echo "---- 1. hermes user ----"
if id hermes >/dev/null 2>&1; then
    echo "  user hermes exists — leaving as-is"
else
    # A group left behind by an earlier user would make a plain
    # `useradd --user-group` fail, so reuse it.
    if getent group hermes >/dev/null 2>&1; then
        group_args=(--gid hermes)
    else
        group_args=(--user-group)
    fi
    useradd --system "${group_args[@]}" --home-dir /var/lib/hermes --no-create-home \
        --shell /usr/sbin/nologin hermes
    echo "  created system user hermes (nologin, home /var/lib/hermes)"
fi
echo ""

# ────────────────────────────────────────────────────────────────
# 2. FHS directories
#
# Code under /opt/hermes and config under /etc/hermes stay root-owned so the
# service cannot rewrite its own code. `hermes` owns only its state and log
# dirs. `install -d` with explicit mode and owner repairs a directory that an
# earlier script left with the wrong ones, and is a no-op on a correct one.
# ────────────────────────────────────────────────────────────────
echo "---- 2. FHS directories ----"
install -d -m 0755 -o root -g root /opt/hermes /opt/hermes/versions /opt/hermes/bin "$PYTHON_DIR"
install -d -m 0755 -o root -g root /etc/hermes
install -d -m 0750 -o hermes -g hermes /var/lib/hermes /var/log/hermes
for dir in /opt/hermes /etc/hermes /var/lib/hermes /var/log/hermes; do
    echo "  $dir: $(stat -c '%a %U:%G' "$dir")"
done
echo ""

# ────────────────────────────────────────────────────────────────
# 3. uv (pinned) and Python 3.14
#
# The installer URL pins the version, not the content: root-only, TLS to the
# vendor. Python goes to /opt/hermes/python because the default uv dir is
# under /root, which hermes.service cannot read (ProtectHome=true).
# ────────────────────────────────────────────────────────────────
echo "---- 3. uv and Python ----"
ensure_uv "$UV_VERSION"
export UV_PYTHON_INSTALL_DIR="$PYTHON_DIR"
# Never fall back to a system interpreter: the venv must link into $PYTHON_DIR.
export UV_PYTHON_PREFERENCE=only-managed
ensure_python "$PYTHON_VERSION"
echo ""

# ────────────────────────────────────────────────────────────────
# 4. systemd units
# ────────────────────────────────────────────────────────────────
echo "---- 4. systemd units ----"
if [ ! -d "$SYSTEMD_SRC" ]; then
    echo "  ERROR: $SYSTEMD_SRC missing — vps-bootstrap.yml must ship deploy/systemd first"
    exit 1
fi
install_units "$SYSTEMD_SRC" /etc/systemd/system
for notifier in "${NOTIFIERS[@]}"; do
    install_notifier "$BOOTSTRAP_DIR/$notifier" "/opt/hermes/bin/$notifier"
done
if [ "$units_changed" = "1" ]; then
    systemctl daemon-reload
fi
if systemctl is-enabled --quiet hermes.service; then
    echo "  hermes.service already enabled"
else
    # No --now: the first deploy starts it, after the release exists.
    systemctl enable hermes.service
fi
# A notifier that failed under the old layout stays listed as failed until
# reset; clear it so `systemctl --failed` only shows live problems.
systemctl reset-failed 'hermes-notify-failure@*' || true
rm -rf "$BOOTSTRAP_DIR"
echo ""

# ────────────────────────────────────────────────────────────────
# 5. /etc/hermes/env.production (written once, see ensure_env_file)
# ────────────────────────────────────────────────────────────────
echo "---- 5. $ENV_FILE ----"
ensure_env_file "$ENV_FILE"
ensure_health_timer "$ENV_FILE"
ensure_daily_timer "$ENV_FILE"
echo ""

# ────────────────────────────────────────────────────────────────
# 6. Storage-box mount (guarded)
# ────────────────────────────────────────────────────────────────
echo "---- 6. Storage-box mount ----"

if [ -z "${STORAGE_BOX_HOST:-}" ] || [ -z "${STORAGE_BOX_USER:-}" ] || [ -z "${STORAGE_BOX_KEY:-}" ]; then
    echo "  WARN: storage-box credentials missing — skipping mount"
    echo "  (add STORAGE_BOX_HOST + STORAGE_BOX_USER + STORAGE_BOX_KEY to the prod env when ready)"
else
    # Ensure mount point + sshfs binary.
    prepare_mount_point /mnt/storage-box
    if ! command -v sshfs >/dev/null 2>&1; then
        echo "  installing sshfs..."
        apt-get update -qq < /dev/null && apt-get install -y sshfs < /dev/null
    fi

    # Storage-box key at a stable location, 0400 hermes:hermes. A subshell
    # scopes the restrictive umask, so it cannot leak into later steps.
    key_path=/home/hermes/.ssh/storage_box_ed25519
    install -d -m 0700 -o hermes -g hermes /home/hermes/.ssh
    (
        umask 0377
        printf '%s\n' "$STORAGE_BOX_KEY" > "$key_path.tmp"
    )
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

    # Ensure the shadow-mode subdir exists. `mkdir -p`, not `install -d`: the
    # latter chmods an existing directory, which a FUSE mount may refuse.
    if [ ! -d /mnt/storage-box/shadow-mode ]; then
        sudo -u hermes mkdir -p /mnt/storage-box/shadow-mode \
            || echo "  WARN: could not create /mnt/storage-box/shadow-mode"
    fi
fi
echo ""

# ────────────────────────────────────────────────────────────────
# 7. sudoers for a non-root CD deploy user (guarded, validated)
# ────────────────────────────────────────────────────────────────
echo "---- 7. deploy sudoers ----"
ensure_deploy_sudoers "$DEPLOY_USER"
echo ""

# ────────────────────────────────────────────────────────────────
# 8. Unattended upgrades with a 03:30 reboot window
# ────────────────────────────────────────────────────────────────
echo "---- 8. automatic security updates ----"
ensure_auto_reboot
echo ""

# ────────────────────────────────────────────────────────────────
# 9. journald retention (ADR-008 §1)
# ────────────────────────────────────────────────────────────────
echo "---- 9. journald retention ----"
ensure_journald_retention
echo ""

echo "==== VPS bootstrap complete ===="
