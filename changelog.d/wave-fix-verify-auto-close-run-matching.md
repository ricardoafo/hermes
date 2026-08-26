### Fixed

- **`scripts/dev/verify_auto_close.py`** — merge-queue run correlation was broken: substring-matched `str(pr_num)` against `display_title`, but every merge-queue run's `display_title` is the literal string `"Merge queue"` (no PR number), so the matcher never found the right run and every PR reported `v3_did_not_run`. Now correlates by `head_sha == pr.mergeCommit.oid` via new `_find_merge_queue_run_id()` helper. PR query updated to fetch `mergeCommit`. Self-test extended with 3 head-sha matching fixtures (positive, negative, empty-sha).
