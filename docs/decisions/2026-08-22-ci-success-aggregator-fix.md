# ci-success aggregator — replace grep-based check with Python allowlist

**Date**: 2026-08-22
**Author**: Maintainer
**Related**: PR #72 (merged red), PR #73 (merged red), issue #78, meta issue #71

## Decision

Extract the `ci-success` aggregator logic from `.github/workflows/ci.yml`
into a testable shell script `scripts/dev/ci-success-check.sh` that uses
Python for proper JSON parsing and a positive-list allowlist of
`{success, skipped}` for accepted results.

## Context

`ci-success` is the single required status check on the `main` branch
ruleset. Its job is to aggregate every other CI job's result and pass
only if all passed. If the aggregator is buggy, the ruleset can't
protect main — any red check slips through.

The previous inline shell:

```bash
if echo "$results" | grep -qE '"result":"(failure|cancelled)"'; then
  echo "::error::One or more required CI gates failed."
  exit 1
fi
```

Assumed compact JSON output from `toJSON(needs)`. In practice
GitHub-Actions pretty-prints:

```text
"result": "failure"   (note the space after `:`)
```

The regex `"result":"failure"` (no space) never matched. Result:
ci-success **always** reported success regardless of actual failures.

Direct evidence: PR #73's failed run (`32534619517`) has ci-success
logs showing the JSON with `"markdown-lint": {"result": "failure"}`
in the payload, and immediately below: `All required CI gates
passed.` — the exact bug in action.

Two PRs — #72 and #73 — merged with red checks because of this. The
ruleset itself was correctly configured (empty `bypass_actors`,
`CI success` required), the aggregator was silently lying to it.

## Alternatives considered

**Fix the grep regex to accept whitespace** (`"result":\s*"…"`):

- One-character change
- Fragile: any future GH-Actions JSON serialization change breaks it again
- Rejected — a shell grep should not be parsing JSON

**Rewrite inline with jq**:

- jq is baked into the runner image
- But jq is NOT on the dev-machine git-bash by default, which broke the
  self-test
- Rejected in favor of Python, which we already require everywhere

**Rewrite inline with Python one-liner in ci.yml**:

- Keeps everything in one file
- But makes it untestable — the logic hides inside a YAML shell block
  where no self-test can exercise it
- Rejected: we've been burned by silent aggregator bugs; testability is
  worth the extraction

**Extract to `scripts/dev/ci-success-check.sh` with self-test** (chosen):

- Mirrors the pattern established by `pr-body-lint.sh`,
  `codeowners-syntax-check.sh`, and `pr-preflight.sh`
- `HERMES_SELF_TEST=1` runs 5 canonical fixtures on every commit that
  touches the script (via pre-commit hook)
- Fixture #3 specifically encodes the PR #72/#73 regression:
  pretty-printed JSON with `"result": "failure"` — the exact shape that
  slipped past grep
- Positive-list allowlist ({success, skipped}) catches any future
  GH-Actions result state ("action_required", "startup_failure", …)
  without needing to chase them

## Consequences

**What this locks in**:

- ci-success now uses Python-based JSON parsing (safe against
  serialization changes)
- Any result outside {success, skipped} fails the aggregator
- Self-test enforces correctness on every script edit
- 5 fixtures document the expected behavior for future readers

**What this forecloses**:

- Cannot silently add a new "accepted" state without editing the
  allowlist. This is intentional — new states must be explicitly
  reviewed
- Can no longer read the aggregation logic inline in `ci.yml`; you
  jump to the script. Acceptable trade for testability

**What we'll revisit**:

- If GH-Actions ever exposes a machine-readable status check API
  that supersedes needs-context aggregation, consider using it
- If we outgrow bash + Python, the aggregator becomes a strong
  candidate for a Node module under `tools/merge/`

## Related concurrent guarantee

The ruleset was already correctly configured (empty `bypass_actors`,
strict:false, required check: `CI success`). No ruleset API PUT was
needed. This entry is scoped to the aggregator bug only — the
governance layer beneath it was doing its job all along.
