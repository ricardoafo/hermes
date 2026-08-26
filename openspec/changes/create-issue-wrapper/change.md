# Change: create-issue-wrapper

**SDD change**: `create-issue-wrapper`
**Date**: 2026-08-26
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#251)

Mirror of engram topic key `sdd/create-issue-wrapper/change`.

## Summary

Introduces `scripts/dev/create-issue.sh` as the mandatory wrapper around `gh issue create` for all repo tooling and agent workflows. Rejects invocations missing `--milestone`, `--assignee`, or type/area/size labels — the metadata classes AGENTS.md § "On issue discipline" requires but has no enforcement gate for today. Ships with a `HERMES_SELF_TEST=1` fixture matrix covering every missing-metadata case. Adds one paragraph to `AGENTS.md` pointing agents at the wrapper.

## Scope

### In

- **NEW**: `scripts/dev/create-issue.sh` — wrapper that:
  - Requires `--title <str>`, `--milestone <str>`, `--assignee <str>`, and `--body <str>` OR `--body-file <path>`.
  - Requires at least one of each label class: `--label type:...`, `--label area:...`, `--label size:...`.
  - Forwards allowlisted flags to `gh issue create` (`--label` repeatable, `--project`, `--web`, `--milestone`, `--assignee`, `--body`, `--body-file`, `--title`).
  - Rejects unknown flags loudly (same allowlist pattern used by `create-pr.sh`).
  - Exit codes: 0 success, 1 gh-auth failure, 2 usage / missing metadata.
- **NEW**: `HERMES_SELF_TEST=1` self-test — asserts every missing-metadata case fails with exit 2 AND no issue is created (dry-run mode when `HERMES_ISSUE_DRY_RUN=1`).
- **MOD**: `AGENTS.md` — one paragraph under §"On issue discipline" (or new section if none exists) telling agents that bare `gh issue create` is disallowed; use this wrapper.
- **NEW**: `changelog.d/wave-create-issue-wrapper.md`.

### Out

- Automated migration of existing bare-`gh issue create` call sites (there are none in the tracked scripts — they're all in agent conversations).
- A CI gate that scans PR diffs for new `gh issue create` calls. Too surface-level — the wrapper's presence plus AGENTS.md guidance suffices; if the pattern recurs, add the gate later.
- Auto-populating body from a template. `create-pr.sh` uses `--body-file`; the same pattern is fine here — templates live in the caller.

## Decisions

### D-1: Enforce ALL THREE label classes (type + area + size)

- **Decision**: refuse invocation if any of `type:`, `area:`, `size:` prefix labels are absent from the `--label` set.
- **Alternatives**: (a) enforce only `type:` — the other two are advisory. (b) accept a single free-form `--label` list and warn if missing.
- **Rationale**: The three label classes correspond to three separate rubrics in this repo: type drives conventional-commit + changelog classification, area drives CODEOWNERS routing, size drives review-time estimates. Missing any one degrades a downstream automated decision. Warnings train operators to ignore them.
- **Cost of reversal**: LOW — relax the check to `type:` only.

### D-2: Wrapper does NOT auto-derive missing metadata

- **Decision**: no `--infer-milestone`, no `--auto-assign-yourself`. Operator must specify.
- **Alternatives**: (a) default `--assignee` to `gh auth --active` login; (b) default `--milestone` to the most recently used milestone.
- **Rationale**: auto-inference silently succeeds when the operator meant to think about the choice. The whole point of the wrapper is to make the choice explicit. Wrong assignee is worse than none.
- **Cost of reversal**: LOW — add optional `--auto-*` flags later without breaking existing calls.

### D-3: Allowlist forwarded flags (like `create-pr.sh`)

- **Decision**: only pre-approved flags are forwarded to `gh issue create`. Unknown flags error out.
- **Alternatives**: pass everything through with `--`.
- **Rationale**: `gh issue create` has 15+ flags. Most (e.g. `--recover`, `--editor`) don't make sense from a wrapper. Explicit allowlist keeps the wrapper's contract auditable.
- **Cost of reversal**: LOW — add flags to the allowlist array as needed.

### D-4: `HERMES_ISSUE_DRY_RUN=1` env for self-test

- **Decision**: self-test sets `HERMES_ISSUE_DRY_RUN=1` and the wrapper prints the `gh issue create` command it WOULD run instead of executing it.
- **Alternatives**: mock `gh` via `PATH` manipulation.
- **Rationale**: env-var toggle is simpler than PATH mocking; the wrapper stays test-visible without a fake `gh` on disk.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Every missing-metadata case exits 2

- **A-1.1**: `bash scripts/dev/create-issue.sh --title x` → exit 2, stderr mentions `--milestone`.
- **A-1.2**: `bash scripts/dev/create-issue.sh --title x --milestone m` → exit 2, stderr mentions `--assignee`.
- **A-1.3**: `bash scripts/dev/create-issue.sh --title x --milestone m --assignee r` → exit 2, stderr mentions `--body`.
- **A-1.4**: `... --label type:chore --label area:tooling` (missing size) → exit 2, stderr mentions `size:`.
- **A-1.5**: Every case above with `HERMES_ISSUE_DRY_RUN=1` produces NO `gh` call (verified by mocked `gh` writing to a log).

### R-2: Fully-specified invocation succeeds

- **A-2.1**: `HERMES_ISSUE_DRY_RUN=1 bash scripts/dev/create-issue.sh --title T --milestone M --assignee A --body B --label type:chore --label area:tooling --label size:s` → exit 0, prints the `gh issue create` command with all flags forwarded, no side effect.

### R-3: Self-test hook

- **A-3.1**: `HERMES_SELF_TEST=1 bash scripts/dev/create-issue.sh` runs the fixture matrix, exits 0 on pass, exits 1 on any assertion failure.

### R-4: AGENTS.md guidance updated

- **A-4.1**: `AGENTS.md` contains a section or paragraph mentioning `scripts/dev/create-issue.sh` and stating that bare `gh issue create` is disallowed in agent workflows.

## Design

Wrapper skeleton (~130 LOC target):

```bash
#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# ── Self-test hook fires when HERMES_SELF_TEST=1 (before flag parsing).

# ── Flag parsing (allowlist forwarded to gh):
ALLOWED_FLAGS=(--title --body --body-file --milestone --assignee --label --project --web)
PASSTHROUGH=()
declare -A SEEN
while [ $# -gt 0 ]; do
    case "$1" in
        --title|--body|--body-file|--milestone|--assignee|--project)
            SEEN["$1"]=1
            PASSTHROUGH+=("$1" "$2")
            shift 2 ;;
        --label)
            LABELS+=("$2")
            PASSTHROUGH+=("$1" "$2")
            shift 2 ;;
        --web)
            PASSTHROUGH+=("$1")
            shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown flag '$1'" >&2; exit 2 ;;
    esac
done

# ── Required-metadata checks:
for req in --title --milestone --assignee; do
    [ -n "${SEEN[$req]:-}" ] || { echo "ERROR: $req required" >&2; exit 2; }
done
[ -n "${SEEN[--body]:-}" ] || [ -n "${SEEN[--body-file]:-}" ] \
    || { echo "ERROR: --body or --body-file required" >&2; exit 2; }

# ── Label class enforcement:
has_type=0; has_area=0; has_size=0
for l in "${LABELS[@]:-}"; do
    [[ "$l" == type:* ]] && has_type=1
    [[ "$l" == area:* ]] && has_area=1
    [[ "$l" == size:* ]] && has_size=1
done
[ "$has_type" = 1 ] || { echo "ERROR: --label type:... required" >&2; exit 2; }
[ "$has_area" = 1 ] || { echo "ERROR: --label area:... required" >&2; exit 2; }
[ "$has_size" = 1 ] || { echo "ERROR: --label size:... required" >&2; exit 2; }

# ── Dry-run or execute:
if [ "${HERMES_ISSUE_DRY_RUN:-0}" = "1" ]; then
    printf 'DRY: gh issue create'
    for arg in "${PASSTHROUGH[@]}"; do printf ' %q' "$arg"; done
    printf '\n'
    exit 0
fi

exec gh issue create "${PASSTHROUGH[@]}"
```

## Test plan

1. `HERMES_SELF_TEST=1 bash scripts/dev/create-issue.sh` — self-test matrix exit 0.
2. Every A-1.x invocation above manually — exit codes match spec.
3. Fully-specified invocation (A-2.1) — dry-run output shows exact `gh issue create` command.
4. `bash scripts/dev/pr-preflight.sh` — all 14 checks green (with `--render-body create-issue-wrapper` for 15).

## Risks

- **R1** — Some agents rely on `--recover`, `--editor`, or interactive `gh issue create`. Mitigation: allowlist expansion is trivial when a legitimate flag surfaces; the wrapper will reject and log the flag, making the gap visible.
- **R2** — Wrapper adds one shell layer between agent and `gh`. Perf cost is negligible (single-digit ms). If it becomes an issue, port to Python.
- **R3** — `gh issue create` output format (issue URL on stdout) may change across `gh` versions. Mitigation: wrapper uses `exec` so the callee's stdout is not touched.

## Dependencies on other changes

- Predecessor: none. Independent of #248 (auto-close v2) and #249 (branch-hygiene) — three separate hardening steps this session.
- Extends `AGENTS.md` §"On issue discipline" (add the section if absent).
