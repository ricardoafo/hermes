# ADR-030: Every merge to `main` deploys to hermes-01 with a version computed from what changed

**Date**: 2026-10-19
**Status**: Accepted (draft approved by the Code Owner on 2026-10-19)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: all of [ADR-028](adr-028-release-cut-every-merge-window.md) (a release PR at every merge window); and in
[ADR-022](adr-022-release-versions-and-deploy-cadence.md) §2 (a version is cut by a release PR), §6 (`DEPLOY_ON`,
hermes-01 deploys only versions from day 0) and §7 (a hand-written `Engine change` line). ADR-022 §3 (`v` tags on
deploy) and §4 (the version on screen), and all of [ADR-027](adr-027-zero-major-versions-until-real-capital.md)
(`0.y.z` until real capital, MINOR for features and engine changes, a manual `1.0.0`), stay as written.

---

## Context

ADR-022 made a release a hand-cut PR, and ADR-028 moved that cut to every merge window. Two days of it showed the
cost:

- A cut only compiles the fragments already on `main`, so the release PR has to wait for every other PR, be re-cut on
  the final `main`, and run CI again. PR #1131 (`0.2.0`) was cut on 18 Oct, waited a day at the end of the list, and
  merged on 19 Oct with the 18 Oct cut: 21 fragments that merged in between are not in its notes.
- Each release is one more PR, one more CI run and one more review for the Code Owner, carrying no code.
- The `Engine change` line is a judgement an agent writes by hand, though ADR-025 already knows which files are
  engine files.

On 2026-10-19 the Code Owner asked for it to be dynamic: as PRs merge they deploy, and the version moves by itself
according to what changed.

Two things make that safe now. hermes-01 is staging until the ADR-024 cutover, and the only shadow on it, v1's
warm-up, is uncounted. And every merge already passes the merge queue, the CD release checks (end-to-end serve cycle,
adapter contracts), the bar-close hold, the health gate with rollback, and the smoke suite.

## Decision

### 1. One push to `main` is one release

Each push to `main` that passes CI goes through CD as today. A merge-queue batch lands as one push, so a batch of
PRs is one release and one deploy; a PR the Code Owner merges directly is its own push. No release PR is opened.

### 2. A push that changes nothing shipped deploys nothing

CD compares the files that go into the release bundle (the `src/` package and its locked dependencies, `config/`,
`deploy/`, the deploy scripts and the shipped experiment files) between the push and the commit of the last `v`
tag. When none changed, CD still runs its release checks, so `main` stays proven, but takes no version and does not
deploy. That covers docs-only and tests-only merges, research batch and results PRs (they add files under
`docs/experiments/`, which the bundle does not carry), and CI-only changes.

### 3. The version is computed

The next version comes from the last `v` tag and what changed since its commit, following ADR-027 §1:

- **MINOR** when a changelog fragment added since then is `added`, `changed`, `deprecated` or `removed`, or when any
  engine file changed (the ADR-025 list in `scripts/dev/train-decision-replay.sh`: `domain/`, `application/`, the
  fill and fee adapters, `config/strategies.yaml`, the pinned experiment data, `uv.lock`);
- **PATCH** otherwise: only `fixed` or `security` fragments, or none (a dependency bump or a chore that touches the
  bundle).

The engine flag is the file list, not a judgement, and it errs towards MINOR: a release whose engine files changed is
MINOR even when ADR-025's replay found no changed decision. The release notes say which engine files changed and
link each PR's replay result.

A computed bump never leaves `0.y.z`. `1.0.0` stays a manual release PR with `--real-capital` (ADR-027 §3).

### 4. The version is computed and stamped under the deploy lock

Two batches merged close together must not get the same number. So the version is computed, stamped into the package
and built inside the `deploy-vps` lock, from the last `v` tag at that moment. A newer push waiting on the lock
replaces an older one still waiting: its commit contains the older one's changes, so both ship as one version.

`pyproject.toml` and `src/hermes/__init__.py` in the repository keep the last released version for local tools; CD
stamps the computed one into the copy it builds. A version is tagged only when its deploy passes the health gate and
the smoke suite (ADR-022 §3). A deploy that rolls back is not tagged, and the next push computes from the same last
tag, so the number is reused for what actually ran.

### 5. The release notes are written by CD

The GitHub release for `v<version>` takes its body from the fragments added since the last `v` tag, grouped by
section, each with its PR, as the `deploy-<sha12>` notes already do (#626). `CHANGELOG.md` keeps its sections up to
`0.2.0` and gains a line pointing at the GitHub releases from then on. Fragments are no longer deleted at a cut; an
occasional chores PR sweeps released ones, and the naming rule already keeps a second PR on one issue on its own
`+<slug>` file.

### 6. The Code Owner's merge is the deploy decision

"Never deploy without the Code Owner" now means: nothing reaches `main` without his approval or his merge, and every
push he lets onto `main` deploys. In particular:

- An engine PR that changes a decision is flagged by ADR-025's replay before he approves it. Merging it is the
  decision to restart any counted shadow clock it touches (ADR-021).
- A deploy restarts serve. That alone restarts no shadow clock: ADR-021 restarts a clock on an altered decision, a
  drift breach or a kill, and serve recovers the bars it missed (#1103). So restarts are safe for v1's uncounted
  warm-up and for counted shadows alike. The bar-close hold keeps a restart off a bar close.
- Agents never dispatch CD, tag, deploy by hand, or merge. `DEPLOY_ON` is retired with the workflow change; the Code
  Owner removes the variable.

## Why

- The version changes when the running code changes, by a rule anyone can rerun, so `v0.7.3` on the console names
  exactly one deploy.
- Release notes can no longer miss fragments: they are written from the push that deploys.
- It removes a PR, a CI run and a review per release, and the judgement in the engine line.
- Every gate a release PR had (CI, AI review, the Code Owner's approval) still runs on the PRs themselves.

## Consequences

### Positive

- hermes-01 runs what is on `main`, within one CD run.
- No release PR waits at the end of the merge list, and no re-cut.

### Negative

- More deploys: one per batch with a shipped change, instead of one per window. Each holds the ops runner and
  restarts serve.
- Minor numbers climb fast: every batch with a feature or an engine file is a MINOR. Until `1.0.0` that is only a
  bigger `y`.
- The workflow change moves the version stamp and the wheel build into the locked deploy path, so the build that is
  gated and the build that ships must stay the same artifact. The change spec shows how.

### Neutral

- `deploy-<sha12>` tags and releases continue, so every deploy still maps to its commit.
- CI load is unchanged; CD's release checks already run on every green `main`.

## What would change this decision

- A counted shadow running (ADR-021) and the Code Owner wanting engine changes batched: then a push with engine
  files waits for a window he picks, and other pushes keep deploying.
- The ADR-024 cutover: staging keeps deploying every push, and prod takes a version only through the staging-to-prod
  gate.
- Real capital (ADR-014): `1.0.0` and later MAJOR bumps keep ADR-006's manual cutover checklist.

## Cross-references

- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — what restarts a shadow clock
- [ADR-022](adr-022-release-versions-and-deploy-cadence.md) — `v` tags on deploy, the version on screen
- [ADR-024](adr-024-four-environments-and-promotion.md) — hermes-01 is staging until the cutover
- [ADR-025](adr-025-train-decision-replay-on-engine-prs.md) — engine files and the decision replay
- [ADR-027](adr-027-zero-major-versions-until-real-capital.md) — `0.y.z` and the bump meanings
- [ADR-028](adr-028-release-cut-every-merge-window.md) — the merge-window release PR this replaces
- Issue #1278
