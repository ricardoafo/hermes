# Release Guide

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole reviewer)
**Applies to**: cutting a versioned release of hermes.
**Description**: How to cut a version with a release PR, how CD tags it on first deploy, and how to check the result.

## The shape of a release

A version is cut by a normal pull request, never by a workflow
([ADR-022](../architecture/adr-022-release-versions-and-deploy-cadence.md) §2;
`release.yml` is gone and is not coming back). Three things happen, in order:

1. A **release PR** sets the version, compiles the changelog and goes through CI, the AI
   review and the Code Owner's approval like any other PR.
2. When the PR merges, `main` carries a version that has not deployed yet. With the
   repository variable `DEPLOY_ON=version`, that is what lets CD deploy to hermes-01
   (ADR-022 §6; only the Code Owner flips the variable).
3. After a good deploy, CD creates the git tag `v<version>` on the shipped commit, with a
   GitHub release whose body is that version's `CHANGELOG.md` section (ADR-022 §3). A
   version that never deploys gets no tag.

Versions are `0.y.z` until a champion trades real money
([ADR-027](../architecture/adr-027-zero-major-versions-until-real-capital.md)): MINOR for
features and any engine change, PATCH for fixes that change no engine decision. The step to
`1.0.0` is a manual cut with `--real-capital`. Merging a release PR deploys to hermes-01
staging, so it is the Code Owner's call; agents open the PR and never merge, tag or deploy.

## Cut a release

From a fresh branch off an up-to-date `main`:

```bash
git fetch origin main
git checkout -b release/0.2.0 origin/main
uv run python scripts/dev/cut-release.py 0.2.0 --engine-change no
```

`cut-release.py` does the mechanical part:

- sets `version` in `pyproject.toml` and `__version__` in `src/hermes/__init__.py`, and
  refreshes `uv.lock`;
- turns `[Unreleased]` plus every `changelog.d/` fragment into a dated `[0.2.0]` section
  under a fresh empty `[Unreleased]`, and deletes the compiled fragments;
- writes the release PR body outside the checkout (`--body-out`, default
  `<tmp>/hermes-release-0.2.0.md`);
- refuses a version that is invalid, retired, or not greater than the current one, an
  engine change on a PATCH bump, and `1.0.0` without `--real-capital`.

Pass `--dry-run` first to print the new section and change nothing. `--engine-change yes`
means the release changes a decision the engine makes (ADR-021), so the Code Owner knows
before approving whether the deploy restarts the shadow clock. The body has an
`Engine change:` line that `pr-body-lint.sh` rejects while it is blank.

Then commit as `chore(release): 0.2.0`, push, and open the PR with the generated body:

```bash
git commit -am "chore(release): 0.2.0"
bash scripts/dev/create-pr.sh --title "chore(release): 0.2.0" --body-file <body from cut-release>
```

## Verify after the deploy

```bash
# The tag and release exist (CD creates them after the first good deploy of the version)
gh release view v0.2.0 --repo ricardoafo/hermes

# The running host reports the same version and the live release (run on hermes-01)
hermes_run version
```

If the deploy health gate fails, `install_release.sh` rolls back to the previous release
and no tag is created. Fix forward with a PATCH release; do not move or delete tags.
Deleting a tag needs the Code Owner.

## Related

- [ADR-022](../architecture/adr-022-release-versions-and-deploy-cadence.md) — release PR, tag on deploy, deploy only versions
- [ADR-027](../architecture/adr-027-zero-major-versions-until-real-capital.md) — `0.y.z` until real capital
- `scripts/dev/cut-release.py` — the cut itself
- `changelog.d/README.md` — fragments that feed the changelog
- [`operator-usage.md`](operator-usage.md) — how auto-deploy works and where to look
- [`environments.md`](environments.md#hermes-01-run-as-staging) — what hermes-01 is until the first champion
