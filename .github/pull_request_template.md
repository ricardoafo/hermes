# Pull request

<!--
PR title MUST follow Conventional Commits format. The type prefix in
the title (feat/fix/docs/chore/refactor/test/perf/build/ci/revert) IS
the change classification — no separate "type" section below.

Examples:
  feat(fifo): add lot-consumption event log
  fix(exchange): retry Binance 5xx with backoff
  docs(guides): clarify squash-merge policy

Full guide: docs/guides/dev-workflow.md
-->

## Summary

<!-- 1-3 sentences: what changes and WHY. Not what — the diff is the what. -->

## Related

<!-- ADR, Q&A note, incident report, prior PR, GitHub issue. Use N/A if none. -->

- ADR: <!-- e.g. ADR-007 §3.2 or N/A -->
- Issue: <!-- e.g. Closes #42 or N/A -->

## Testing

<!--
How was this verified? Which tests were added or extended? What was
MEASURED (not asserted)? Paste concrete artifacts — log lines, coverage
delta, benchmark output — over "I ran it and it worked".

For docs: at minimum "markdownlint-cli2 passes with zero warnings across N files".
For code: which pytest files? Coverage delta? Benchmark numbers if perf?
-->

## Zero-warnings compliance

All applicable linters pass with **zero** warnings (markdownlint, ruff,
mypy, safety, pip-audit — whichever apply). No `# noqa`, no `# type:
ignore`, no `<!-- markdownlint-disable -->` added. If a suppression was
unavoidable, explain in the Risk section below.

## Risk

<!--
What could break? Who feels it first? Is there a rollback plan?
For docs / chore: usually "None — non-runtime change".
For domain / adapters: describe blast radius honestly.
-->

## Follow-ups

<!--
Out-of-scope items surfaced during this PR. Link issues or say "None".
Prevents scope creep and makes the PR reviewable in <15 minutes.
-->

---

<!--
Reviewer: @ricardoafo (auto-requested via CODEOWNERS; the main-protection
ruleset requires his approval). Merge: squash-only, through the merge queue.
Author cannot self-approve. Bot cannot merge.
See docs/guides/dev-workflow.md for the full discipline.
-->
