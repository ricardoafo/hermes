### Fixed

- **Automation-triggered CI runs no longer require manual approval**
  (`.github/workflows/merge-queue.yml`,
  `.github/workflows/auto-update-pr-branches.yml`, GitHub App
  `hermes-automation-bot`, repo variable `HERMES_AUTOMATION_APP_ID`,
  repo secret `HERMES_AUTOMATION_APP_KEY`): both automation workflows now
  mint a short-lived installation token from the dedicated
  `hermes-automation-bot` GitHub App (via `actions/create-github-app-token`)
  and pass that token to `pulls/{n}/update-branch` and `pulls/{n}/merge`
  calls, instead of the workflow's default `GITHUB_TOKEN`. GitHub's
  built-in loop-prevention rule holds workflows triggered by
  `github-actions[bot]` commits in `action_required` status until manually
  approved — App-authored commits are a distinct actor and bypass this
  gate. The result: fully hands-off "idea → merge" flow, no per-PR
  approval clicks on downstream CI. GitHub Apps are the mature-PDP
  pattern for this problem — no rotation drift (short-lived
  installation tokens self-issued from a stable private key), fine-grained
  per-repo permissions (Contents R/W + Pull requests R/W only), dedicated
  identity in the git log. Falls back to `GITHUB_TOKEN` if the App
  variable is unset (e.g., forks), so the workflows continue to function
  with the approval friction rather than breaking outright.
