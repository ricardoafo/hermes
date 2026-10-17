# Tracking issue

Every SDD change is tracked by exactly one GitHub issue (per ADR-009 §6
and the Rust RFC tracking-issue pattern). This file records the link so
`scripts/dev/render-pr-body.sh` can auto-inject `Closes #N` into the PR
body and `pr-preflight.sh --render-body <change>` can validate the loop
end-to-end.

Related: issue #815 (the console half, the version in the header, is built
here; the status and Telegram half stays with the versioning work, so this
change does not close it).

<!-- Data block below is parsed by scripts/dev/render-pr-body.sh -->

issue: 833
url: <https://github.com/ricardoafo/hermes/issues/833>
<!-- closes: <N2>    optional, repeatable; add when bundling multiple issues -->
