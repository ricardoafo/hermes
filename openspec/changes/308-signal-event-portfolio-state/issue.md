# Tracking issue

Every SDD change is tracked by exactly one GitHub issue (per ADR-009 §6
and the Rust RFC tracking-issue pattern). This file records the link so
`scripts/dev/render-pr-body.sh` can auto-inject `Closes #N` into the PR
body and `pr-preflight.sh --render-body <change>` can validate the loop
end-to-end.

This change is tracked by #308 (<https://github.com/ricardoafo/hermes/issues/308>).
Parent issue: #301 (serve shadow loop). #301 stays open; this PR does not
close it. Related: #273, closed by a spec-only PR (#274) before the tracker it
specified was built.

If the change closes MORE than one issue (bundled work), add one
`closes: <N>` line per secondary issue below the `url:` line. The
render script emits `Closes #N` for each and deduplicates against the
primary `issue:` value.

<!-- Data block below is parsed by scripts/dev/render-pr-body.sh -->

issue: 308
url: <https://github.com/ricardoafo/hermes/issues/308>
<!-- closes: <N2>    optional, repeatable; add when bundling multiple issues -->
<!-- closes: <N3> -->
