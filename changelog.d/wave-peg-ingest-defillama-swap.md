### Changed

- **Peg data ingest** — swapped provider from CoinGecko (free tier changed 2026-08, now requires API key + tighter rate limits) to DefiLlama (`coins.llama.fi/chart`, no auth). Same manifest key layout (`peg/<coin>/-/<yyyy>-<mm>`), same on-disk namespacing (`<data-dir>/peg/<coin>/<yyyy>-<mm>.json`) — downstream SA-9 depeg overlay reader (Spec 8) sees no shape change. Reverses decision D-2 of `data-ingest-agg-trades-and-peg`. Closes #245.
- **Lint hygiene** — `.claude/` sub-agent worktree scratch now ignored by both `.gitignore` and `.markdownlint-cli2.jsonc` (belt-and-suspenders; the class of accidental worktree-pollution PRs — see #242 — is closed off at both git and lint layers).
