#!/usr/bin/env bash
# Train decision replay for engine PRs (issue #798, ADR-025).
#
# Replays the roster strategy over the Train split on the PR's base tree and
# on the tree CI checked out, then compares their decisions. Nothing is
# priced, measured or logged as a trial. Only changes to the files below
# can change a decision, so any other changeset passes without a replay.
#
# Usage (CI, pull_request events):
#   BASE_SHA=<sha> PR_NUMBER=<n> GH_TOKEN=<token> GITHUB_REPOSITORY=<owner/repo> \
#     bash scripts/dev/train-decision-replay.sh
#
# Environment:
#   HERMES_DATA_DIR   Binance Vision archive, read-only (default /srv/hermes-data)
#   CODE_OWNER        Whose comment approves a decision change (default ricardoafo)
#   REPLAY_WORKDIR    Scratch directory (default $RUNNER_TEMP or /tmp)
#
# Exit codes:
#   0 - no engine file changed, no decision changed, or changes the Code Owner approved
#   1 - decisions changed without the Code Owner's approval, or the replay could not run

set -uo pipefail

ENGINE_PATHS='^src/hermes/(domain|application)/|^src/hermes/adapters/(fills|fees)/|^config/strategies\.yaml$|^docs/experiments/[^/]+/(fee-history\.csv|data-manifest\.json)$|^uv\.lock$'
LABEL="decisions-changed"

: "${BASE_SHA:?BASE_SHA is required}"
export HERMES_DATA_DIR="${HERMES_DATA_DIR:-/srv/hermes-data}"
CODE_OWNER="${CODE_OWNER:-ricardoafo}"
workdir="${REPLAY_WORKDIR:-${RUNNER_TEMP:-/tmp}}/train-decision-replay"
head_dir="$(pwd)"

engine_files=$(git diff --name-only "$BASE_SHA" HEAD | grep -E "$ENGINE_PATHS" || true)
if [[ -z "$engine_files" ]]; then
  echo "No engine file changed against the base; no decision can change."
  exit 0
fi
echo "Engine files changed:"
echo "$engine_files"

if [[ ! -d "$HERMES_DATA_DIR/klines" ]]; then
  echo "::error::Train data not found under $HERMES_DATA_DIR on this runner (ADR-025 mounts it read-only on the CI pool)"
  exit 1
fi

label() {
  local method="$1" path="$2" data="${3:-}"
  [[ -n "${GH_TOKEN:-}" && -n "${PR_NUMBER:-}" ]] || return 0
  curl -fsS -o /dev/null -X "$method" \
    -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    ${data:+-d "$data"} \
    "https://api.github.com/repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/labels$path" \
    || echo "::warning::could not $method the $LABEL label"
}

rm -rf "$workdir"
mkdir -p "$workdir"
base_dir="$workdir/base"
git worktree add --detach "$base_dir" "$BASE_SHA" >/dev/null || exit 1
trap 'git -C "$head_dir" worktree remove --force "$base_dir" >/dev/null 2>&1' EXIT

# Each tree runs its own engine. The base runs its own copy of the replay when
# it has one; before #798 merged it had none, so the head's copy drives it.
base_script="$base_dir/scripts/backtest/decision_replay.py"
[[ -f "$base_script" ]] || base_script="$head_dir/scripts/backtest/decision_replay.py"

echo "Replaying Train on the head tree..."
uv run --frozen --no-sync python scripts/backtest/decision_replay.py record \
  --out "$workdir/head.jsonl" || exit 1

echo "Replaying Train on the base tree ($BASE_SHA)..."
(cd "$base_dir" && uv sync --frozen --quiet && uv run --frozen --no-sync python "$base_script" \
  record --out "$workdir/base.jsonl") || exit 1

uv run --frozen --no-sync python scripts/backtest/decision_replay.py diff \
  "$workdir/base.jsonl" "$workdir/head.jsonl" > "$workdir/diff.txt"
rc=$?
cat "$workdir/diff.txt"
fingerprint=$(sed -n 's/^Changes fingerprint: //p' "$workdir/diff.txt")
case "$rc" in
  0)
    label DELETE "/$LABEL"
    exit 0
    ;;
  1)
    label POST "" "{\"labels\":[\"$LABEL\"]}"
    if [[ -n "${PR_NUMBER:-}" ]] \
      && python3 scripts/dev/decision_change_approval.py \
        "$GITHUB_REPOSITORY" "$PR_NUMBER" "$CODE_OWNER" "$fingerprint"; then
      echo "::notice::Train decisions changed; the Code Owner approved the change."
      exit 0
    fi
    echo "::error::Train decisions changed. A changed decision restarts the shadow clock (ADR-021), so the PR body must cite a comment of the Code Owner on this PR that quotes the changes fingerprint $fingerprint (ADR-025)."
    exit 1
    ;;
  *)
    echo "::error::the decision diff could not run"
    exit 1
    ;;
esac
