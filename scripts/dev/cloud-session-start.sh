#!/usr/bin/env bash
# Install the git hooks when a Claude Code session starts in the cloud (#847).
#
# A cloud session clones the repo fresh, so none of the pre-commit or
# commit-msg hooks are installed and a writer can commit past every local
# gate without noticing. Local clones install the hooks once by hand
# (dev-workflow.md) and are left alone.
#
# Called by:
#   - .claude/settings.json SessionStart hook
#   - Pre-commit hook when THIS script changes (self-test)
#
# Env vars:
#   CLAUDE_CODE_REMOTE — "true" in a cloud session; anything else does nothing
#   HERMES_SELF_TEST   — set to 1 to run built-in cases and exit
#
# Exit codes:
#   0 — always: a failed install is reported, never allowed to stop the session

set -uo pipefail

HOOK_TYPES=(pre-commit commit-msg)

# install_hooks <installer...>
# Runs the installer once per hook type; prints one line saying what happened.
install_hooks() {
    if [[ "${CLAUDE_CODE_REMOTE:-}" != "true" ]]; then
        return 0
    fi
    local args=()
    local hook_type
    for hook_type in "${HOOK_TYPES[@]}"; do
        args+=(--hook-type "$hook_type")
    done
    if "$@" install "${args[@]}" >/dev/null 2>&1; then
        echo "git hooks installed: ${HOOK_TYPES[*]}"
    else
        echo "WARNING: could not install the git hooks; run: uv run pre-commit install ${args[*]}"
    fi
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    cat > "$tmp/fake-pre-commit" <<FAKE
#!/usr/bin/env bash
echo "\$*" >> "$tmp/calls"
FAKE
    cat > "$tmp/broken-pre-commit" <<'FAKE'
#!/usr/bin/env bash
exit 1
FAKE
    chmod +x "$tmp/fake-pre-commit" "$tmp/broken-pre-commit"

    out=$(CLAUDE_CODE_REMOTE=false install_hooks "$tmp/fake-pre-commit")
    if [[ -z "$out" && ! -e "$tmp/calls" ]]; then
        echo "PASS: a local session installs nothing"
    else
        echo "FAIL: a local session ran the installer: $out"; failed=1
    fi

    out=$(CLAUDE_CODE_REMOTE=true install_hooks "$tmp/fake-pre-commit")
    if [[ "$(cat "$tmp/calls" 2>/dev/null)" == "install --hook-type pre-commit --hook-type commit-msg" ]]; then
        echo "PASS: a cloud session installs the pre-commit and commit-msg hooks"
    else
        echo "FAIL: installer called with: $(cat "$tmp/calls" 2>/dev/null)"; failed=1
    fi

    out=$(CLAUDE_CODE_REMOTE=true install_hooks "$tmp/broken-pre-commit"); rc=$?
    if [[ "$rc" == 0 && "$out" == WARNING:* ]]; then
        echo "PASS: a failed install warns and does not stop the session"
    else
        echo "FAIL: failed install gave rc=$rc output: $out"; failed=1
    fi

    if [[ "$failed" == 1 ]]; then
        echo ""
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo ""
    echo "All self-tests passed."
    exit 0
fi

install_hooks uv run pre-commit
exit 0
