# Pull request

<!--
PR title MUST follow Conventional Commits format.
Examples: feat(fifo): add lot-consumption event log
          fix(exchange): retry Binance 5xx with backoff
          docs(guides): clarify squash-merge policy
Full guide: docs/guides/dev-workflow.md
-->

## Summary

<!-- 1-3 sentences: what changes and WHY. Not what — the diff is the what. -->

## Related

<!-- ADR, Q&A note, incident report, prior PR, GitHub issue. Use N/A if none. -->

- ADR: <!-- e.g. ADR-007 §3.2 or N/A -->
- Issue: <!-- e.g. #42 or N/A -->

## Change type

<!-- Check exactly one. Must match the type prefix in the PR title. -->

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `chore` — tooling / deps / meta
- [ ] `refactor` — behavior-preserving internal change
- [ ] `test` — test-only
- [ ] `perf` — performance (include measurement below)
- [ ] `build` — build system / packaging
- [ ] `ci` — CI workflow
- [ ] `revert` — reverts a prior commit (reference SHA below)

## Testing

<!--
How was this verified? Which tests were added or extended?
For docs: at minimum "markdownlint-cli2 passes with zero warnings".
For code: which pytest files? Coverage delta if measured.
-->

## Zero-warnings compliance

- [ ] All applicable linters pass with **zero** warnings (markdownlint, ruff,
      mypy, safety, pip-audit — whichever apply). No `# noqa`, no `# type:
      ignore`, no `<!-- markdownlint-disable -->` added. If a suppression was
      unavoidable, explain in the body.

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
Reviewer: @ricardoafo (auto-assigned via CODEOWNERS).
Merge: squash-merge only. Author cannot self-approve. Bot cannot merge.
See docs/guides/dev-workflow.md for the full discipline.
-->
