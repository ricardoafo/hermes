# Tracking issue

Every SDD change is tracked by exactly one GitHub issue (per ADR-009 §6
and the Rust RFC tracking-issue pattern). This file records the link so
`scripts/dev/render-pr-body.sh` can auto-inject `Closes #N` into the PR
body and `pr-preflight.sh --render-body <change>` can validate the loop
end-to-end.

Fill in the issue number + URL after running `gh issue create ...`.

If the change closes MORE than one issue (bundled work), add one
`closes: <N>` line per secondary issue below the `url:` line. The
render script emits `Closes #N` for each and deduplicates against the
primary `issue:` value.

<!-- Data block below is parsed by scripts/dev/render-pr-body.sh -->

issue: 35
url: <https://github.com/ricardoafo/hermes/issues/35>
<!-- closes: <N2>    optional, repeatable; add when bundling multiple issues -->
<!-- closes: <N3> -->
