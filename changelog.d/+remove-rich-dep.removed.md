- **`rich` from runtime dependencies** (`pyproject.toml`, `uv.lock`): grep of
  `src/` and `scripts/` shows zero `import rich` / `from rich` usages —
  the dep was declared but never called. Removing it: (a) reduces attack
  surface, (b) unblocks `import-linter>=2.13` (which requires `rich>=14.2`
  as its own transitive dep, and can install it freely once we don't pin
  a conflicting `<14`), (c) supersedes the pending Dependabot bump #158
  (bumping unused code is worse than removing it). Should we later need
  rich for CLI pretty-printing (e.g. `hermes health` colored table), we
  re-add explicitly with a clear consumer in the same commit.
