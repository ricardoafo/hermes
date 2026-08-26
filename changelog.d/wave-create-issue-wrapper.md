### Added

- **`scripts/dev/create-issue.sh`** — mandatory wrapper around `gh issue create`. Rejects invocations without `--milestone`, `--assignee`, `--body`/`--body-file`, and at least one label from each of the three classes (`type:`, `area:`, `size:`). Prevents the metadata-drift class of process violations exemplified by #245. `HERMES_SELF_TEST=1` fixture matrix + `HERMES_ISSUE_DRY_RUN=1` for non-destructive verification. Closes #251.
