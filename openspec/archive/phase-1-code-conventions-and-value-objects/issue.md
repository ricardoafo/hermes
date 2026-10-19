# Tracking issue

Every SDD change is tracked by exactly one GitHub issue per ADR-009 §6
and the Rust RFC tracking-issue pattern. `scripts/dev/render-pr-body.sh`
reads this file to auto-inject `Closes #N` into the PR body.

**Note**: this change bundles two features that close TWO issues. Primary
tracking is #106 (conventions guide); the PR body will hand-add
`Closes #98` per the workaround in `preferences/sdd-multi-issue-close`
(until #97 ships multi-close support).

<!-- Data block below is parsed by scripts/dev/render-pr-body.sh -->

issue: 106
url: <https://github.com/ricardoafo/hermes/issues/106>
