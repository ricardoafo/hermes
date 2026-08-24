### Changed

- **`_build-wheel.yml` workflow name**: `Build wheel (reusable)` →
  `Build wheel`. The `(reusable)` suffix was dev-speak leaking into a
  user-facing string; the underscore prefix on the filename already
  signals "not directly triggered." Cosmetic only — no behavior change.
