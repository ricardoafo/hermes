### Changed

- **Process hardening wave 5** (`pyproject.toml`, `scripts/dev/pr-preflight.sh`,
  `docs/guides/ai-authored-tests.md`, `tests/unit/test_pytest_timeout_integration.py`,
  `AGENTS.md`, `SECURITY.md`, `.github/dependabot.yml`,
  `docs/guides/{dev-workflow,local-setup,ai-development-lifecycle,ai-authored-tests}.md`,
  `docs/decisions/2026-08-23-binance-testnet-klines.md`): closes two drifts from the
  post-#138 review. (1) pytest-timeout added at `>=2.3,<3` with a 60s
  default and `timeout_method = "thread"` (Windows-safe) in
  `[tool.pytest.ini_options]`. A test that hangs past the cap now fails
  LOUDLY with a stack trace instead of silently draining sub-agent /
  CI time — root cause of the wave-4 slot-12 sub-agent kill was a real
  production float-drift infinite loop that no watchdog surfaced. The
  `tests/unit/test_pytest_timeout_integration.py` self-test runs an
  inner pytest via subprocess against a `while True` marked
  `@pytest.mark.timeout(2)` and asserts the inner run terminated
  non-zero within the 2s cap — regressions in the plugin config break
  this file first. `pr-preflight.sh` self-test's tool list gained
  `pytest-timeout` so the pre-flight rejects environments where the
  plugin is missing. `docs/guides/ai-authored-tests.md` gained the
  Hypothesis-and-timeouts note near the property-testing section.
  (2) Prose scrub of the underlying operator-owned bot GitHub
  username to the project-neutral alias `ai-bot` in docs (AGENTS.md,
  SECURITY.md, four guides under `docs/guides/`, one decisions-journal
  entry). Functional occurrences preserved: `.github/dependabot.yml`
  `assignees` (real GH username required), `git config --local
  user.name` / `user.email` commands (real git identity required),
  and `gh auth switch --user` invocation (real GH CLI account name).
  AGENTS.md gained a naming-convention paragraph in "The three roles"
  explaining the split so future PRs replace the underlying username
  in prose but never in config. Placeholder-named fragment per
  `changelog.d/README.md` — aggregator ignores non-numeric prefixes.
