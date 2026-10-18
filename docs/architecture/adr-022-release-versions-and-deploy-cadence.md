# ADR-022: Release versions and deploy cadence — cut versions by release PR, tag them on deploy, and deploy only versions once the shadow clock runs

**Date**: 2026-10-06
**Status**: Accepted — §1 superseded by [ADR-027](adr-027-zero-major-versions-until-real-capital.md) (`0.y.z` until real capital)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: [ADR-006](adr-006-versioning-strategy.md) §1 "Version bump rules", the release-cut mechanics only.
SemVer, the `1.0.0-shadow.N` pre-release scheme (with [ADR-014](adr-014-pre-registration-binds-real-capital.md)'s
calendar), numbered ledger migrations and versioned config schemas stay as written.
**Amends**: [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md), adding what triggers a deploy and which
tags CD writes.

---

## Context

ADR-006 chose SemVer for the release surface and put every cut in a manual `release` workflow that would bump the
version, move `[Unreleased]` into a versioned section and tag git. No cut ever happened:

- `pyproject.toml` and `src/hermes/__init__.py` still declare `0.0.0`, so `hermes version` and the supervisor's MCP
  handshake report `0.0.0` for every build.
- The only git tags are `deploy-<sha12>`. CD writes one, plus a GitHub release with notes built from the
  `changelog.d/` lines added since the previous deploy, after each good deploy (#626).
- `CHANGELOG.md` has only `[Unreleased]`. About 190 fragments wait in `changelog.d/` for
  `scripts/dev/aggregate-changelog.py --apply`, which nothing runs.
- The 2026-10-03 CI/CD target design deleted `release.yml`, so the cut step has had no home since.

Every green merge to `main` also deploys to hermes-01. During the warm-up shadow that is cheap, and each deploy
exercises the deploy path. [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) changes the stakes: once the counted
shadow starts (day 0, issue #797), a decision-changing engine change restarts the 90-day clock. A deploy per merge
gives that many chances to restart it by accident, and makes "what was running on day N" a SHA hunt.

The Code Owner asked how versioning works, found the gaps above, and approved all seven decisions below on 2026-10-06.

## Decision

### 1. Shadow builds are `1.0.0-shadow.N`

The first cut is `1.0.0-shadow.1`, taking every fragment now in `changelog.d/`. Each later shadow cut increments `N`.
`1.0.0` stays reserved for the first release cleared for real capital, under ADR-014.

### 2. A version is cut by a release PR, not a workflow

A script run from a fresh branch off `main`:

- sets `version` in `pyproject.toml` and `__version__` in `src/hermes/__init__.py`, and a test keeps them equal;
- compiles `changelog.d/` with `aggregate-changelog.py --apply`, renames `[Unreleased]` to `[<version>] - <date>` and
  removes the compiled fragments;
- refuses a version that is not valid SemVer or not greater than the current one;
- renders the release PR body with that changelog section.

The release PR goes through CI, AI review and the Code Owner's approval like any other PR. No version is bumped
automatically. `release.yml` is not brought back.

### 3. CD tags `vX.Y.Z` the first time a version deploys

After a good deploy, CD reads the version from the shipped wheel. If `v<version>` has no tag yet, CD creates it on the
shipped commit, with a GitHub release whose body is that version's `CHANGELOG.md` section. If the tag exists, CD leaves
it. `deploy-<sha12>` tags and releases continue unchanged, so every deploy stays traceable to its exact commit.

The tag marks what reached hermes-01, not what merged. A version that never deploys gets no tag.

### 4. The version is shown wherever the operator looks

The console header, `hermes status` and the Telegram deploy note show `v<version>` beside the short SHA of the live
release. `hermes version` already prints both (issue #539).

### 5. The Code Owner decides when to cut

There is no schedule. A cut is a product decision, usually at a milestone or before a deploy he wants.

### 6. From day 0, hermes-01 deploys only versions

A repository variable `DEPLOY_ON` takes `every-green` (today's behaviour, the default) or `version`. The Code Owner
flips it to `version` as a day-0 step beside the issue #797 command; agents never flip it. With `version`, CD still
builds and runs the release-candidate gate on every green `main`, so `main` stays proven, but skips the deploy unless
the commit carries a version that has not deployed yet.

Exact-SHA deploys, the build-once release-candidate gate, the bar-close hold and bake with rollback do not change.

### 7. Every release PR says whether it changes the engine

The release PR body has an `Engine change: yes/no` line that the author must fill in, and `pr-body-lint.sh` rejects
it blank. `yes` means a decision-changing engine change under ADR-021, so the Code Owner knows before approving
whether the deploy restarts the shadow clock. Issue #798's replay check is the evidence for the answer once it lands.

## Why

- A human-readable version is what ADR-006 wanted in notifications, the console and post-mortems; a SHA alone is not
  enough to say "yesterday's build".
- A release PR reuses the existing gates (CI, AI review, Code Owner approval) instead of adding a workflow, which fits
  the 2026-10-03 decision to delete `release.yml`.
- Tagging on deploy rather than on merge keeps the tag honest: `v<version>` always means "this ran on hermes-01".
- Deploying only versions after day 0 turns each clock restart into a deliberate, reviewed decision.

## Consequences

### Positive

- `CHANGELOG.md` becomes a readable history of releases instead of a growing `[Unreleased]` block.
- The console, CLI and Telegram all name the same version.
- After day 0, the shadow only changes when the Code Owner approves a release PR.

### Negative

- Fixes reach hermes-01 only when someone cuts a version, after day 0. An urgent fix needs a patch cut.
- One more PR per release, and the `Engine change` line is a human judgement until issue #798 backs it.

### Neutral

- CI load is unchanged: every green `main` still builds and gates. Only the deploy step is skipped.

## What would change this decision

- Real capital (ADR-014): `1.0.0` and later MAJOR bumps add the manual cutover checklist ADR-006 already describes.
- A second deploy target (staging, issue #671): revisit whether staging takes every green `main` while production
  takes versions.

## Cross-references

- [ADR-006](adr-006-versioning-strategy.md) — SemVer, migrations, config schemas
- [ADR-014](adr-014-pre-registration-binds-real-capital.md) — pre-release calendar
- [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md) — CD and runners
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — shadow clock and day 0
- [`changelog.d/README.md`](../../changelog.d/README.md) — fragment naming
- Issues: #811 this ADR, #812 release cut and `1.0.0-shadow.1`, #813 `v` tags on deploy, #814 version-only deploys,
  #815 version on screen, #797 day 0, #798 engine replay check

## Pending

- Issue #812, then issue #813 and issue #815; issue #814 before day 0. Workflow PRs one at a time, per ADR-018.
