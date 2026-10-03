#!/usr/bin/env bash
# Diff-scoped mutation run for new domain + application code (#160).
#
# Mutates only the lines of src/hermes/domain/ and src/hermes/application/
# that differ from a base ref (default origin/main), runs the unit tests
# of those layers against each mutant, and prints the kill rate. Use it
# before opening a PR that adds or changes logic in either layer.
#
# Config: cosmic-ray-diff.toml (the base ref there is overridden by
# --base). The weekly scheduled run uses cosmic-ray.toml and is not
# affected.
#
# Invocation:
#   bash scripts/dev/mutation-diff.sh [--base <ref>] [--floor <percent>]
#
# Exit codes:
#   0 — no mutants on changed lines, or kill rate >= floor
#   1 — kill rate below floor (survivors listed by cr-report)
#   2 — usage error

set -euo pipefail

BASE="origin/main"
FLOOR="80"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --base) BASE="${2:?--base needs a ref}"; shift 2 ;;
        --floor) FLOOR="${2:?--floor needs a number}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 2 ;;
    esac
done

work=$(mktemp -d -t mutation-diff.XXXXXX)
trap 'rm -rf "$work"' EXIT
config="$work/config.toml"
session="$work/session.sqlite"

sed "s|^branch = .*|branch = \"$BASE\"|" cosmic-ray-diff.toml > "$config"

uv run cosmic-ray init "$config" "$session"
uv run cr-filter-git --config "$config" "$session"

pending=$(uv run python - "$session" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
total = db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
done = db.execute("SELECT COUNT(*) FROM work_results").fetchone()[0]
print(total - done)
PY
)
if [[ "$pending" == "0" ]]; then
    echo "mutation-diff: no mutants on lines changed since $BASE."
    exit 0
fi
echo "mutation-diff: $pending mutants on lines changed since $BASE."

uv run cosmic-ray baseline "$config"
uv run cosmic-ray exec "$config" "$session"

# cr-rate counts filtered (skipped) mutants as killed, which would
# inflate the rate to ~100%; count only the mutants that actually ran.
kill=$(uv run python - "$session" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
rows = db.execute(
    "SELECT LOWER(test_outcome) FROM work_results WHERE LOWER(worker_outcome) != 'skipped'"
).fetchall()
ran = len(rows)
survived = sum(1 for (outcome,) in rows if outcome == "survived")
print(f"{100 * (ran - survived) / ran:.2f}" if ran else "100.00")
PY
)
echo "mutation-diff: kill rate ${kill}% (floor ${FLOOR}%)"

if uv run python -c "import sys; sys.exit(0 if float('$kill') >= float('$FLOOR') else 1)"; then
    exit 0
fi
uv run cr-report --surviving-only --show-diff "$session"
exit 1
