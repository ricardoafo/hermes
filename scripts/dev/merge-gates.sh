#!/usr/bin/env bash
# merge-gates.sh — the tree gates the merge queue runs on a batch tree.
#
# Usage: merge-gates.sh <fail-file> <base-sha>
#
#   <fail-file>  receives the name of the first gate that failed (empty
#                when every gate passed), so the queue can tell the PR
#                author what to fix without parsing a log
#   <base-sha>   the `main` tip the batch was built on; gitleaks scans
#                only the commits the batch adds on top of it
#
# Exit 0 when every gate passes, 1 on the first failure. The failing
# gate's name is also printed on stderr.
#
# Why a script and not workflow steps: bisect runs the gates once per
# candidate tree, from a loop. A step list runs once per job. The queue
# runs the copy of this file pinned from `main` before any PR is merged
# in, so a PR cannot change the gates its own batch is judged by.
#
# Only content-derived checks live here. PR metadata gates (title, body,
# changelog) do not change when `main` advances and stay in PR CI.

set -euo pipefail

fail_file="${1:?usage: merge-gates.sh <fail-file> <base-sha>}"
base="${2:?usage: merge-gates.sh <fail-file> <base-sha>}"
: > "$fail_file"

gate() {
  local name="$1"
  shift
  echo "::group::gate: ${name}"
  if ! "$@"; then
    echo "::endgroup::"
    echo "gate failed: ${name}" >&2
    printf '%s\n' "$name" > "$fail_file"
    exit 1
  fi
  echo "::endgroup::"
}

# Installs run only when the runner image does not bake the tool in. The
# versions are pinned so a red gate means the tree changed, not the tool.
install_markdownlint() {
  command -v markdownlint-cli2 >/dev/null 2>&1 \
    || sudo npm install -g markdownlint-cli2@0.23.2
}

install_gitleaks() {
  local version=8.30.1
  local sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
  if [ "$(gitleaks version 2>/dev/null)" != "$version" ]; then
    curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${version}/gitleaks_${version}_linux_x64.tar.gz" \
      -o /tmp/gitleaks.tar.gz
    echo "$sha256  /tmp/gitleaks.tar.gz" | sha256sum -c -
    sudo tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks
    sudo chmod +x /usr/local/bin/gitleaks
  fi
}

gate "install markdownlint-cli2" install_markdownlint
gate "markdownlint" bash scripts/dev/ci-checks.sh markdown
gate "install gitleaks" install_gitleaks
gate "gitleaks" gitleaks git . "--log-opts=${base}..HEAD" --redact --no-banner --verbose
gate "uv sync" uv sync --frozen
gate "ruff format" bash scripts/dev/ci-checks.sh format
gate "ruff check" bash scripts/dev/ci-checks.sh lint
gate "mypy" bash scripts/dev/ci-checks.sh types
gate "lint-imports" bash scripts/dev/ci-checks.sh imports
# pytest names its failures in the fail file, so the PR comment says
# which tests broke, not just "pytest". At most five names, comma-joined.
run_pytest() {
  local log status names
  log=$(mktemp)
  set +e
  uv run pytest tests/unit/ tests/integration/ -n 4 -q 2>&1 | tee "$log"
  status=${PIPESTATUS[0]}
  set -e
  if [ "$status" -ne 0 ]; then
    names=$(grep -E '^(FAILED|ERROR) ' "$log" | awk '{print $2}' | head -5 | paste -sd ',' - | sed 's/,/, /g')
    echo "gate failed: pytest" >&2
    printf 'pytest: %s\n' "${names:-see the run log}" > "$fail_file"
    rm -f "$log"
    exit 1
  fi
  rm -f "$log"
}

echo "::group::gate: pytest"
run_pytest
echo "::endgroup::"
