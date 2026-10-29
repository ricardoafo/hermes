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

5. **`isolation: worktree` REQUIRED for parallel sub-agent spawns.**
   When two or more sub-agents run in parallel via the Agent tool,
   each MUST be spawned with `isolation: "worktree"`. Sharing the
   working tree causes `git checkout -b` collisions that surface as
   stash-corruption during recovery (root cause of a 30-min recovery
   during 2026-08-25 Batch 1 delegation).

6. **PR body via `render-pr-body.sh <change>` only — never hand-authored.**
   `bash scripts/dev/render-pr-body.sh <change>` renders from
   `openspec/changes/<change>/change.md` + `issue.md`, auto-injects
   `Closes #N`, and produces the exact shape the `pr-body-lint.sh`
   validator expects (4 sections: Summary / Related / Testing / Risk).
   Hand-authored bodies routinely miss one section and fail CI
   (verified across Batch 1/2 delegation).

7. **`changelog.d/<issue>-<type>.md` fragment is an acceptance criterion,
   not a follow-up.** The fragment ships in the same PR as the change.
   The `changelog-check.sh` gate rejects `feat/fix/refactor/perf`
   without a fragment (per sdd-process-hardening D-3). Bypass with
   `ALLOW_CHANGELOG_MD_EDIT=1` is for forks, not for "I'll add it
   later" — do it now.

8. **`scripts/dev/no-vague-test-assertions.py` is a pre-PR checkpoint.**
   Run it against your new tests BEFORE the final push. Every
   `assert x == y` needs an informative message. The pre-commit
   hook rejects bare assertions, so leaving them in blocks the WIP
   push.

9. **Fix up ONCE at the start, not reactively per commit.** Run
   `uv run ruff format . && uv run ruff check . --fix && npx
   --no-install markdownlint-cli2 --fix "**/*.md"` before the first
   WIP commit. This preempts 3-5 pre-commit-hook fix-loops per commit
   later (each loop costs ~5-10 tool uses — verified in Batch 1/2
   RCA on 2026-08-25).

10. **PR body: explain WHAT is being implemented, not just what
    shipped.** The Summary section (or a dedicated subsection within
    Related) should include a technical explanation the reviewer can
    read to understand the intent + design without also opening the
    diff. A terse one-sentence summary saves 5 seconds of writing
    but costs 10 minutes of the reviewer's context reconstruction.
    Rules of thumb:
    * 1-3 sentences: what ships + why + main risk (mandatory —
      matches the existing template)
    * Followed by: 2-5 paragraphs (or bulleted subsections)
      explaining the KEY design decisions, the shape of the new
      abstractions, and how they interact with existing code
    * Include a `mermaid` diagram WHEN it clarifies architecture
      (module boundaries), flow (sequence of events), or state
      (finite-state-machine transitions). Do NOT include mermaid
      when a short paragraph would say the same thing — diagrams
      that repeat prose are noise.
    * Cite existing file paths + line numbers when referencing
      code that already exists (e.g., "reuses `TITLE_REGEX` from
      `scripts/dev/create-pr.sh:75`").

11. **Test tiers and TDD posture (MANDATORY for code changes).**
    Read docs/guides/ai-authored-tests.md before writing tests.
    In your final report, state:
    * Tiers touched: which of unit / property / contract /
      integration / mutation you added or changed tests in. Put the
      same list in the PR body's ## Testing section as
      `Test tiers touched: unit, property` (create-pr.sh refuses the
      PR without it).
    * TDD posture: whether you wrote the tests BEFORE or AFTER the
      production code.
    Every new module under src/hermes/ needs a
    tests/unit/**/test_<name>.py in the same commit (pre-commit hook
    test-file-adjacency). New logic in src/hermes/domain/ or
    src/hermes/application/: run `bash scripts/dev/mutation-diff.sh`
    before the final push and report the kill rate.
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
- [`docs/guides/dev-workflow.md`](../../docs/guides/dev-workflow.md)
  — git/gh discipline (no `--no-verify`, conventional commits,
  squash-merge policy).
