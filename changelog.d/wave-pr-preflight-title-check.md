### Added

- **`pr-preflight.sh` — `PR title semantic` check** — validates HEAD commit subject against the same `TITLE_REGEX` `create-pr.sh` enforces (conventional-commit prefix + lowercase first char after `:`). Closes the class of drift where a rebase or amend leaves an uppercase subject that `amannn/action-semantic-pull-request@v6` rejects at CI-time, forcing a close-and-reopen cycle. Restores Tier-2 defer from #215 (`sdd-process-hardening`). Bypass: `SKIP_TITLE_CHECK=1` for rebase-in-progress workflows. Closes #255.
