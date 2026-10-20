#!/usr/bin/env bash
# Assemble the release bundle CD ships to hermes-01: the wheel, the
# hash-pinned requirements from uv.lock, the config/ YAML and the deploy
# scripts, as hermes-release-<sha12>.tar.gz in the current directory.
#
# Run from the repository root at the release commit. The CD bundle job
# runs it, and so does the deploy job when the version it computes under
# the deploy lock differs from the one the wheel was built with (ADR-030 §4).
#
# Usage: assemble_bundle.sh <sha12> <dir-holding-the-wheel>
#
# Every repository path copied here must match BUNDLE_PATHS in
# scripts/deploy/next_version.py, or a change to it would not deploy;
# tests/unit/deploy/test_cd_workflow.py checks that.
set -euo pipefail

sha12=${1:?usage: assemble_bundle.sh <sha12> <wheel-dir>}
wheel_dir=${2:?usage: assemble_bundle.sh <sha12> <wheel-dir>}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# The wheel keeps its original filename: uv rejects a renamed one.
cp "$wheel_dir"/hermes-*.whl "$stage/"
if [ "$(ls "$stage"/hermes-*.whl | wc -l)" -ne 1 ]; then
    echo "::error::expected exactly one wheel in $wheel_dir"
    exit 1
fi

# Same lock, same SHA-256 hashes as CI (ADR-010 section 4). The VPS
# installs from this file with --require-hashes.
uv export --frozen --no-dev --no-emit-project --output-file "$stage/requirements.txt"
if ! grep -q -- '--hash=sha256:' "$stage/requirements.txt"; then
    echo "::error::uv export wrote no hashes"
    exit 1
fi

cp -r config "$stage/config"
# hermes serve reads the signed pre-registration (kill rules) and the
# fee history (pre-reg §6.1) from the release root (#353 D-6).
install -d "$stage/docs/experiments/swing-mvp1"
install -m 0644 docs/experiments/swing-mvp1/pre-registration-v1.md \
    docs/experiments/swing-mvp1/fee-history.csv "$stage/docs/experiments/swing-mvp1/"
# The units run these from /opt/hermes/current/deploy/ (health ping,
# failure notifier, backup): each release carries its own copy.
install -d "$stage/deploy" "$stage/bin"
install -m 0755 deploy/hermes-health-ping.sh deploy/hermes-notify-failure.sh deploy/hermes-notify-crash.sh deploy/hermes-daily-line.sh deploy/hermes-backup.sh deploy/hermes-console.sh "$stage/deploy/"
install -m 0755 scripts/deploy/install_release.sh scripts/deploy/health_gate.sh \
    scripts/deploy/rollback.sh scripts/deploy/schema_guard.py "$stage/bin/"
# install_release.sh installs the units and timers that changed, as
# vps-bootstrap does, so a unit change ships with its release (#626).
install -d "$stage/deploy/systemd"
install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer "$stage/deploy/systemd/"

tar -czf "hermes-release-${sha12}.tar.gz" -C "$stage" .
tar -tzf "hermes-release-${sha12}.tar.gz"
