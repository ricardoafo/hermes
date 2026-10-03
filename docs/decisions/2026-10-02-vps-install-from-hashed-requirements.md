# VPS installs from hashed requirements plus the CI wheel

**Date**: 2026-10-02
**Author**: AI Bot on behalf of the Code Owner
**Related**: ADR-010 §1 (`uv sync --frozen` as the install command) and §4
(SHA-256 hash pinning),
[`openspec/changes/179-cd-first-real-deploy/change.md`](../../openspec/changes/179-cd-first-real-deploy/change.md)
(D-4), GH #179

## Decision

The VPS installs a release with `uv pip install --require-hashes -r
requirements.txt` followed by `uv pip install --no-deps <wheel>`, not with
`uv sync --frozen`; this departs from ADR-010 §1 for the VPS install step
only.

## Context

ADR-010 §1 says the VPS runs `uv sync --frozen`. That command needs the
project source tree and `uv.lock` on the box, and it installs the project
from source. hermes-01 has no source checkout, and the first real deploy
should run the exact wheel CI built and tested.

The `bundle` job in `.github/workflows/cd.yml` therefore ships one tarball:
the CI-built wheel (original filename), `requirements.txt` from `uv export
--frozen --no-dev --no-emit-project`, `config/` and the deploy scripts.
`install_release.sh` installs the requirements with `--require-hashes`,
then the wheel with `--no-deps`.

Alternatives passed over:

- **`uv sync --frozen` on the box**: needs a checkout there and installs
  from source, so the box runs code CI never built.
- **Three loose `scp` calls (wheel, requirements, config)**: three places to
  drift apart.
- **Extending `_build-wheel.yml`**: `release.yml` also calls it, so a CD
  need would change the release path.

## Consequences

- Same lock and same SHA-256 hashes as CI (ADR-010 §4): `uv export` writes
  the hashes from `uv.lock`, and `--require-hashes` rejects any mismatch
  before `current` moves. ADR-010 lines 47-48 already allow `uv export` for
  interop.
- The wheel itself is not hash-checked on the box. It arrives over the
  tailnet from the same workflow run that built it.
- ADR-010 §1 keeps its text (ADRs are immutable); this entry is the record
  of the departure. Anyone reading §1 for the VPS should read this entry
  next.
- If a future change moves the box to a source checkout, this entry is
  superseded by a new one.
