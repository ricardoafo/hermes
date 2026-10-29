# ADR-027: Release versions are `0.y.z` until a champion trades real money; `1.0.0-shadow.N` is retired

**Date**: 2026-10-17
**Status**: Accepted (merged in PR #1092)
**Deciders**: Maintainer
**Supersedes**: [ADR-022](adr-022-release-versions-and-deploy-cadence.md) §1 ("Shadow builds are `1.0.0-shadow.N`");
the `0.x.y` and `1.0.0-shadow.N` pre-release lines of [ADR-006](adr-006-versioning-strategy.md) §1; and, in
[ADR-014](adr-014-pre-registration-binds-real-capital.md) decision 5, the sentence "`1.0.0-shadow.N` covers shadow mode
for as long as it runs". The rest of ADR-022 (release PR, `v` tags on deploy, version-only deploys, the
`Engine change` line), ADR-006's MAJOR/MINOR/PATCH meanings from `1.0.0` on, and ADR-014's real-capital calendar
stay as written.

---

## Context

ADR-006 planned `1.0.0-shadow.N` for a shadow mode that would trade "tiny real-capital sums" from August 2026 and
reach real capital by the end of 2026. ADR-022 kept that scheme and cut `v1.0.0-shadow.1` on 2026-10-06; it is the
only version cut so far.

The plan has moved since:

- The shadow is paper trading. No strategy trades real money, and none can before ADR-014's `LIVE_APPROVED` sign-off.
- Strategy v2 failed its Train gate. Candidates now come through the research funnel (ADR-026), each needing Train,
  Validation and a 90-day counted shadow (ADR-021) before it can be a champion.
- ADR-024 adds four environments; prod (hermes-01 after the cutover) is the only one that will hold trade keys.

So `1.0.0` is months away, and every build until then would carry it as a pre-release. SemVer 2.0.0 already has a
place for this stage: major version zero is initial development, and `1.0.0` is the first release people can rely
on. `-shadow` also reads as a build property when it is really a strategy's lifecycle stage (ADR-021): one build
runs a champion, warm-up shadows and the lab at the same time.

The Code Owner raised it on 2026-10-17: Hermes should be at something like `0.2.4` now and reach `1.0.0` when it is
productive.

## Decision

### 1. Releases are `0.MINOR.PATCH` until real capital

Every cut from now on is `0.Y.Z`. No new `-shadow.N` cut is made; the cut script refuses one.

- **MINOR** (`0.2.0` to `0.3.0`): new features, any breaking change, and any release whose `Engine change` line is
  `yes` (a decision-changing engine change under ADR-021).
- **PATCH** (`0.2.0` to `0.2.1`): fixes that change no engine decision.

So a PATCH cut never restarts a counted shadow clock; the cut script refuses `--engine-change yes` on a PATCH bump.

### 2. The 2026-10-06 cut counts as `0.1.0`

`v1.0.0-shadow.1` keeps its tag, GitHub release and `CHANGELOG.md` section: they record what ran, and rewriting a
published tag would break the `deploy-<sha12>` notes chain and anything already linking to it. In the `0.y.z` order
it counts as `0.1.0`, so the next cut is `0.2.0` and the cut script accepts any `0.y.z` above `0.1.0` from the
retired `1.0.0aN` package version.

`hermes.release_version` still reads `1.0.0aN` as `v1.0.0-shadow.N`, so an old wheel, tag or changelog section is
named the same way it was.

### 3. `1.0.0` is the first release a champion trades real money with

`1.0.0` is cut for the release that runs a champion with real money on prod, after the ADR-024 cutover and the
ADR-014 `LIVE_APPROVED` sign-off (so not before 2027-01-01). The cut script refuses to leave `0.y.z` for anything but
`1.0.0`, and refuses `1.0.0` without `--real-capital`, so it cannot happen by a slip. From `1.0.0` on, ADR-006's
MAJOR/MINOR/PATCH meanings and its cutover checklist for MAJOR bumps apply, and an engine change still needs at least
a MINOR bump.

### 4. Nothing else orders versions

The only "must be newer" check is the cut script's. The rest of the release path does not compare versions:

- CD deploys a version when its `v<version>` tag does not exist yet (ADR-022 §3, §6), so `v0.2.0` deploys once.
- `install_release.sh` installs into `/opt/hermes/versions/<sha12>` and switches by SHA, so a lower package version
  is not a downgrade to it.
- `hermes shadow-start` records the installed version in the day-0 record as text; nothing compares it.
- The console, `hermes status` and the Telegram note print the label.

`v0.y.z` releases are created with `prerelease: false` (only `-shadow.` tags were marked pre-release) and
`make_latest: false`, so the `deploy-<sha12>` release stays the latest one and the notes chain is unchanged.

## Why

- `0.y.z` says what is true: Hermes is not yet trusted with money. `1.0.0` then marks the one moment that matters,
  the first real-capital release.
- MINOR for engine changes ties the version number to the shadow clock: a reader of `0.4.2` to `0.4.3` knows no
  counted shadow restarted.
- Keeping the old tag avoids rewriting published history; mapping it to `0.1.0` keeps the order readable.

## Consequences

### Positive

- Version numbers match the product stage, and `1.0.0` keeps its meaning.
- The console, `hermes status` and Telegram drop the long `-shadow.N` suffix.

### Negative

- The tag list has one odd entry, `v1.0.0-shadow.1`, above `v0.2.0` in SemVer order. Tools that sort tags by SemVer
  would put it last; hermes tooling sorts release tags by date.
- PEP 440 also orders `1.0.0a1` above `0.2.0`, so a plain `pip install --upgrade` from the old wheel would not move.
  Hermes never upgrades in place: each release installs into its own SHA directory.

### Neutral

- No change to CD, the deploy trigger, the release PR or CI load.

## What would change this decision

- Real capital before a champion is Done (ADR-014 changed): `1.0.0` moves with it.
- A second product line on the same repository: a version per line instead of one.

## Cross-references

- [ADR-006](adr-006-versioning-strategy.md) — SemVer, migrations, config schemas
- [ADR-014](adr-014-pre-registration-binds-real-capital.md) — real-capital calendar
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — shadow clock and engine changes
- [ADR-022](adr-022-release-versions-and-deploy-cadence.md) — release PR, tags, deploy cadence
- [ADR-024](adr-024-four-environments-and-promotion.md) — environments and the hermes-01 cutover
- [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) — research funnel
- Issue #1086
