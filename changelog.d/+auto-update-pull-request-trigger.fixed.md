- **Newly-opened PRs no longer sit in BEHIND state until the next
  push:main** (`.github/workflows/auto-update-pr-branches.yml`): added
  `pull_request: [opened, reopened, ready_for_review]` trigger to the
  auto-update workflow. Previously the workflow fired only on
  `push:main`, so a PR opened while main was already ahead of its
  branch waited for ANOTHER merge to trigger the freshness pass — the
  reviewer's first impression was a "This branch is out-of-date"
  banner. The new triggers cover: opened (fresh PR), reopened (closed
  → reopened flow), ready_for_review (draft → ready promotion). Each
  event fires exactly one workflow run, updates the branch if BEHIND,
  and no-ops otherwise. Zero-cost when the branch is already
  up-to-date.
