### Fixed

- **`uv.lock` drift**: regenerated lockfile against current `pyproject.toml`.
  Dependabot #157 (cyclonedx-python-lib bump) landed pyproject.toml changes
  without a clean lockfile regen; subsequent `uv run` invocations kept
  updating the lock on the fly, tripping pre-commit's "files modified by
  hook" gate on every PR. Wave 7 (#183) worked around it via
  `UV_FROZEN=1`. This PR is the real fix: `uv lock` run cleanly, three
  transitive updates picked up as a byproduct (`py-serializable` 1.1.2 →
  2.1.0, `python-telegram-bot` 21.11.1 → 22.8, `rich` 13.9.4 → 15.0.0
  from #170 + import-linter's own transitive). Closes #184.
