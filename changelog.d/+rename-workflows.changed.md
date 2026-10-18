- **Workflow renames + build-wheel extraction** (`.github/workflows/cd.yml`,
  `.github/workflows/merge-queue.yml`, `.github/workflows/mutation.yml`,
  `.github/workflows/_build-wheel.yml`, `.github/workflows/release.yml`,
  `docs/guides/*.md`, `deploy/README.md`, `deploy/bootstrap-vps.sh`,
  `scripts/dev/setup-branch-protection.sh`, `scripts/dev/gh-runner/README.md`,
  `tools/merge/decisions.cjs`): renamed `deploy.yml` → `cd.yml`
  (internal `name:` `CD to VPS` → `CD`), `pr-revalidate.yml` →
  `merge-queue.yml` (`name: Merge queue` unchanged), and
  `mutation.yml` internal name `Mutation testing (cosmic-ray)` →
  `Mutation` (the tool is an implementation detail). Extracted the
  duplicated wheel-build step from `cd.yml` and `release.yml` into a
  reusable workflow at `.github/workflows/_build-wheel.yml` (underscore
  prefix = not directly triggered). Both callers now invoke
  `uses: ./.github/workflows/_build-wheel.yml`; `release.yml`'s `build`
  job preserves `needs: verify` so the verify → build → gh-release order
  stands. `merge-queue.yml`'s self-dispatch (`createWorkflowDispatch`
  target) updated to the new filename so the queue continues to chain
  candidates after a merge. Docs, script comments, and the
  `deploy/README.md` mermaid diagram updated to match the new filenames.
  Placeholder-named fragment per `changelog.d/README.md` — aggregator
  ignores non-numeric prefixes.
