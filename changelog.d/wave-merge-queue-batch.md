### Changed

- The merge queue gates and merges every approved PR in one pass: it
  merges them into one tree, oldest approval first, runs the gates once,
  lands the batch back to back, and bisects a red batch to find the PRs
  that broke it, which get one comment naming the failing gate (#413).
