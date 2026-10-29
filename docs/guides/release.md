# Release Guide

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole reviewer)
**Applies to**: how a version of hermes is numbered, deployed and tagged.
**Description**: How every merge to `main` deploys with a computed version, how CD tags it, and how to check the result.

## The shape of a release

There is no release PR
([ADR-030](../architecture/adr-030-every-merge-deploys-with-a-computed-version.md)). Each
push to `main` whose CI passes goes through CD, and one merge-queue batch is one push:

1. **Does it ship?** `scripts/deploy/next_version.py` compares the push with the commit of
   the last `v` tag. When no file the release bundle carries changed (docs, tests, CI,
   research batch and results PRs), CD runs its release checks and stops: no version, no
   deploy.
2. **Which version?** MINOR when a changelog fragment added or edited since that tag is
   `added`, `changed`, `deprecated` or `removed`, or when any engine file changed (the
   ADR-025 list); PATCH otherwise. Versions stay `0.y.z` until a champion trades real money
   ([ADR-027](../architecture/adr-027-zero-major-versions-until-real-capital.md)).
3. **Build and deploy.** The wheel is built with that version stamped into
   `pyproject.toml` and `hermes/__init__.py`; the repository copy keeps the last cut
   version. The deploy job recomputes the version under the deploy lock, so two batches
   merged close together never get one number, and rebuilds the bundle when it moved.
4. **Tag.** After the health gate and the smoke suite pass, CD creates `v<version>` on the
   shipped commit, with the deploy's notes (the fragments and PRs since the previous
   deploy) as the release body. A deploy that rolls back is not tagged, and the next push
   reuses its number. When the tag itself cannot be created after a good deploy, the run
   goes red after the Telegram announcement: the Code Owner creates the tag on that commit
   before the next merge, or the next push would reuse a number that is already live.

The Code Owner's merge is the deploy decision. Agents never dispatch CD, tag, deploy by
hand or merge.

## The step to `1.0.0`

`1.0.0` is the first release a champion trades real money with (ADR-027 §3). It is the one
manual cut left, a release PR from a fresh branch off `main`:

```bash
uv run python scripts/dev/cut-release.py 1.0.0 --engine-change yes --real-capital
```

## Verify after a deploy

```bash
# The tag and release exist (CD creates them after a good deploy of a new version)
gh release view v0.3.0 --repo ricardoafo/hermes

# The running host reports the same version and the live release (run on hermes-01)
hermes_run version
```

The CD run's "Compute the version" step prints the decision: `ships`, the previous and new
version, the bump, and the engine files and fragments that set it.

If the deploy health gate fails, `install_release.sh` rolls back to the previous release
and no tag is created. Fix forward; do not move or delete tags. Deleting a tag needs the
Code Owner.

## Related

- [ADR-030](../architecture/adr-030-every-merge-deploys-with-a-computed-version.md) — every merge deploys with a computed version
- [ADR-027](../architecture/adr-027-zero-major-versions-until-real-capital.md) — `0.y.z` until real capital
- [ADR-022](../architecture/adr-022-release-versions-and-deploy-cadence.md) — tags on deploy, the version on screen
- `scripts/deploy/next_version.py` — the ship decision and the bump
- `changelog.d/README.md` — fragments, whose types set the bump
- [`operator-usage.md`](operator-usage.md) — how auto-deploy works and where to look
- [`environments.md`](environments.md#hermes-01-run-as-staging) — what hermes-01 is until the first champion
