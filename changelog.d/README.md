# CHANGELOG fragments

One-bullet-per-PR fragments that `scripts/dev/aggregate-changelog.py` merges
into `CHANGELOG.md [Unreleased]` at release-cut time. Introduced by process
hardening wave 4 (drift-eval `drift-eval/2026-08-22-post-wave2`) to eliminate
the structural CHANGELOG conflict that hit every PR in the #124-#132 queue —
each PR touching the same `[Unreleased] → ### Added` section forces every
non-first merger to rebase.

## Why fragments

Before wave 4, every feature/fix PR edited `CHANGELOG.md` directly. When two
PRs raced to merge, the second one hit a merge conflict on the changelog even
though the PRs had zero code overlap. Fragments give each PR its own file, so
merges only conflict if they touch the exact same feature.

## Naming convention

Name the fragment **before** the PR exists, so the PR never needs a rename
commit. A push after approval dismisses the approval (#420).

`<issue-number>-<type>.md`: the default.

- `<issue-number>`: the tracking issue the PR closes (every PR has one, per
  AGENTS.md). Issues and PRs share GitHub's number space, so the number never
  collides with another PR's fragment.
- `<type>`: one of `added`, `changed`, `deprecated`, `removed`, `fixed`,
  `security`, matching the Keep-a-Changelog sections.

`+<slug>.<type>.md`: an orphan fragment, for a PR with no issue.

- `<slug>`: lowercase letters, digits, `.`, `_` and `-`.
- At release time the aggregator recovers the PR number from the squash
  commit that added the file (subject ending in `(#<pr>)`).

Examples:

- `355-added.md`: feature for issue #355
- `410-security.md`: security fix for issue #410
- `+ingest-timeouts.fixed.md`: fix with no tracking issue

Fragments named `<pr-number>-<type>.md` (the earlier convention) still work.

Any other name is an error: the aggregator and the release-cut script refuse
to run, and a unit test fails in CI, so a note can never be left out of a
release (#1172). The legacy `wave-*.md` notes were converted to orphan
fragments for that reason.

## Format

Each fragment is a single Markdown bullet (or nested bullets under one
top-level bullet). No section headers — the type comes from the filename. The aggregator
and the release cut refuse any other shape.

```markdown
- **Slack notifier adapter** (`src/hermes/adapters/notifier/slack_adapter.py`):
  concrete `NotifierPort` for the reports/rich channel per the 2026-08-23
  decisions-journal entry. Closes #132.
```

The aggregator inserts each fragment as-is under the correct section, in
number order (ascending, oldest first). Orphans take their resolved PR
number; any that cannot be resolved come last, by slug.

## Backwards compatibility

`scripts/dev/changelog-check.sh` (the "CHANGELOG was updated" CI gate) accepts
EITHER a direct edit to `CHANGELOG.md` OR the addition of a fragment file
under `changelog.d/`. Small docs-only PRs may still update the changelog
directly if that's easier — the fragment pattern is the DEFAULT for
feat/fix/refactor/perf, not a hard requirement.

## Aggregation

Fragments are compiled when a version is cut (ADR-022), by the release-cut
script, from a fresh branch off `main`:

```bash
uv run python scripts/dev/cut-release.py 0.2.0
# or, at a merge window (ADR-028): pick the version from the entries
uv run python scripts/dev/cut-release.py --next --engine-change no
```

It merges the hand-written `[Unreleased]` entries and every numbered or
orphan fragment into a dated `## [<version>] - <date>` section under a fresh
empty `[Unreleased]`, deletes the compiled fragments, sets the version and
writes the release PR body. `--dry-run` prints the new section and changes
nothing.

`scripts/dev/aggregate-changelog.py` still prints the merged fragments for
review; the release-cut script uses it to read them.
