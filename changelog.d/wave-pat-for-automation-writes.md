### Fixed

- **Automation-triggered CI runs no longer require manual approval**
  (`.github/workflows/merge-queue.yml`,
  `.github/workflows/auto-update-pr-branches.yml`,
  repo secret `HERMES_QUEUE_PAT`): both automation workflows now use a
  fine-grained Personal Access Token (scope: `contents:write`,
  `pull-requests:write` on `hermes` only) instead of `GITHUB_TOKEN` when
  calling `pulls/{n}/update-branch` and `pulls/{n}/merge`. Without this,
  every automated merge-commit was authored by `github-actions[bot]`,
  which triggers GitHub's built-in loop-prevention rule: workflows
  triggered by bot-authored commits enter `action_required` status and
  wait for manual approval — defeating the point of hands-off
  automation. PAT-authored commits are attributed to the token owner
  (a human/service account) and run CI as normal push events. The PAT
  path falls back to `GITHUB_TOKEN` if the secret is unset, so the
  workflows continue to function (with the approval friction) in forks
  or downstream repos that don't provision the secret. See the
  `github-token` inline comments in each workflow for context.
