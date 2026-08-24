# Sub-agent prompt checklist — checkpoint discipline

Copy-paste block to include in every non-trivial sub-agent launch
prompt. Aligns with AGENTS.md § On sub-agent delegation →
"Sub-agent rate-limit resilience".

**When to include this block**: any sub-agent launched to write code
across ≥2 files, implement a Part of a multi-Part spec, or run a
chain longer than ~15 tool uses. Trivial one-file mechanical edits
do NOT need this discipline.

## Copy-paste block

Paste the section below verbatim into the sub-agent's launch prompt,
after the task description and before "Do NOT" constraints. Fill the
`<wip-branch-name>` placeholder before pasting.

```text
## Checkpoint discipline (rate-limit resilience — MANDATORY)

You may be interrupted mid-flight by a rate limit, harness kill, or
network drop. Anything not committed AND pushed is lost. Follow these
rules to make your work salvageable:

1. **Commit a WIP checkpoint** every ~30 tool uses OR whenever a
   distinct Part of the task completes (whichever comes first). Use
   `wip:` as the commit-message prefix — the conventional-commits
   pre-commit hook accepts `wip:` at commit-msg stage during your
   run because it is NOT one of the enforced types. WIP commits WILL
   be squashed or amended into `feat:` / `chore:` form before the
   final PR opens.

2. **Push the WIP branch to origin** immediately after each WIP
   commit:
       git push origin <wip-branch-name>
   On the second and later pushes to a rewritten branch:
       git push --force-with-lease origin <wip-branch-name>
   This makes your in-flight work recoverable via `git fetch` if you
   die mid-flight.

3. **Save an engram checkpoint** after each significant milestone
   (Part complete, test suite passes, PR body drafted). Topic key:
       sdd/<change-name>/apply-progress
   Use `mem_save` with type=`architecture` and a title like
   "<change-name> — Part N complete". If prior progress exists at
   the same topic key, READ it via `mem_search` +
   `mem_get_observation` and MERGE — do not overwrite.

4. **Before opening the final PR**: squash or amend every `wip:`
   commit into conventional-commits form (`chore(x): ...`,
   `feat(x): ...`, etc). The commit-msg hook rejects `wip:` when the
   PR opens, so leaving one in place blocks the merge.
```

## Recovery pattern (for the operator, when a sub-agent dies)

1. `git fetch origin <wip-branch>` — pick up whatever the sub-agent
   managed to push.
2. `mem_search` for `sdd/<change-name>/apply-progress` — recover the
   last milestone summary in engram.
3. Continue from the last WIP commit. Do NOT open the PR until every
   `wip:` commit is squashed/amended into conventional-commits form.
4. Run `bash scripts/dev/pr-preflight.sh` locally before pushing the
   final rewrite.

## Related

- [`AGENTS.md § On sub-agent delegation`](../../AGENTS.md#on-sub-agent-delegation)
  — the norm this checklist enforces.
- [`~/.claude/CLAUDE.md § SDD Workflow`](../../.claude/CLAUDE.md) —
  Apply-Progress Continuity rule (read-merge-write, don't overwrite).
- [`docs/guides/dev-workflow.md`](../../docs/guides/dev-workflow.md)
  — git/gh discipline (no `--no-verify`, conventional commits,
  squash-merge policy).
