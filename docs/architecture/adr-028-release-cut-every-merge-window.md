# ADR-028: A release is cut at every merge window

**Date**: 2026-10-18
**Status**: Superseded by ADR-030 (accepted, merged in PR #1110)
**Deciders**: Maintainer
**Superseded by**: [ADR-030](adr-030-every-merge-deploys-with-a-computed-version.md) (every merge deploys with a
computed version)
**Supersedes**: [ADR-022](adr-022-release-versions-and-deploy-cadence.md) §5 ("The Code Owner decides when to cut.
There is no schedule."). The rest of ADR-022 and all of [ADR-027](adr-027-zero-major-versions-until-real-capital.md)
stay as written.

---

## Context

ADR-022 §5 left the cut to the Code Owner with no schedule. In practice only one version was cut between 6 and 18
October while 96 changelog entries merged, so the version said nothing about what was running. On 2026-10-18 the
Code Owner asked for the version to move with every batch of fixes and features.

Merges already arrive in batches: the Code Owner merges at fixed Madrid windows, from a list posted before each one.
`DEPLOY_ON=version` means only a cut version reaches hermes-01, which is staging until the ADR-024 cutover.

## Decision

### 1. One release PR per merge window

After the window's other PRs have merged, an agent cuts a release from that `main` and opens the release PR. It is the
last item on that window's list. A window with no new changelog entries gets no release PR.

The Code Owner merging it is the decision to release; an unmerged release PR is closed when the next one is opened.
Agents never merge a release PR, never cut outside this rule without being asked, and never tag or deploy by hand.

### 2. The bump is computed

`scripts/dev/cut-release.py --next --engine-change yes|no` picks the version from the current one and the entries
being released, following ADR-027 §1:

- PATCH when every entry is `Fixed` or `Security` and the engine line is `no`;
- MINOR otherwise.

The engine line is `yes` unless the agent can show the release changes no engine decision (ADR-021); the release PR
body says why. The step to `1.0.0` stays a manual cut with `--real-capital` (ADR-027 §3).

### 3. What merging does

Merging the release PR deploys it to hermes-01 under `DEPLOY_ON=version` (ADR-022 §6) and tags `v<version>` on the
first deploy (ADR-022 §3). A deploy restarts serve; the v1 warm-up shadow is uncounted, so that is safe. Once a
counted shadow runs, a release with `Engine change: yes` restarts its clock, which the Code Owner sees on the PR before
merging.

## Why

- The version changes as often as the code people use changes, so `v0.4.2` on the console names one batch.
- The cut rides the existing window, so it costs the Code Owner one extra merge, not a separate decision.
- Computing the bump removes the judgement call from all but the engine line, which already exists for this purpose.

## Consequences

### Positive

- Every window's merges reach staging as one named, tagged version.
- The changelog grows one small section per window instead of a large one per milestone.

### Negative

- One more PR per window, and one deploy per window.
- A release PR changes `uv.lock`'s project version line, so a dependency PR merged after it in the same window needs
  `main` merged in. Putting the release PR last avoids it.

### Neutral

- No workflow change: CD already deploys only cut versions.

## What would change this decision

- A counted shadow running (ADR-021): a release per window could restart its clock too often. Then `Engine change:
  yes` releases wait for a window the Code Owner picks, and only PATCH releases follow this cadence.
- The ADR-024 cutover: staging and prod may take versions on different cadences.

## Cross-references

- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — shadow clock and engine changes
- [ADR-022](adr-022-release-versions-and-deploy-cadence.md) — release PR, tags, version-only deploys
- [ADR-024](adr-024-four-environments-and-promotion.md) — staging on hermes-01 until the cutover
- [ADR-027](adr-027-zero-major-versions-until-real-capital.md) — 0.y.z and bump rules
- Issue #1109
