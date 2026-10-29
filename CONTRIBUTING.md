# Contributing

Thanks for your interest in Hermes. It's a single-maintainer project, so the maintainer
reviews every change. These notes are here to help your issue or pull request land.

## Before you start

- **Bugs and ideas:** open an issue using one of the templates. For anything larger than a
  small fix, wait for a reply on the issue before you write code. The change may cross an
  architecture decision.
- **Security bugs:** never in a public issue. See [`SECURITY.md`](SECURITY.md).
- **Architecture:** the [ADRs](docs/architecture/) are the source of truth. A change that
  goes against one needs a new or amended ADR first.

## Setting up

You need Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv tool install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
uv run pytest tests/unit
```

More detail is in [`docs/guides/local-setup.md`](docs/guides/local-setup.md).

## Making a change

1. Fork the repository and create a short-lived branch from `main`, named like
   `fix/fifo-lot-order` or `feat/kraken-fees`.
2. Write the test first where you can. The test tiers are described in
   [`docs/guides/test-tiers.md`](docs/guides/test-tiers.md).
3. Keep the change focused. One concern per pull request.
4. Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages
   and the PR title. The subject starts lowercase, for example
   `fix(ledger): keep lot order on partial fills`.
5. Add a changelog fragment under [`changelog.d/`](changelog.d/README.md) for any
   user-visible change.
6. Run the checks locally before you push:

   ```bash
   bash scripts/dev/ci-checks.sh format
   bash scripts/dev/ci-checks.sh lint
   bash scripts/dev/ci-checks.sh types
   uv run pytest tests/unit
   ```

7. Open the pull request with the template filled in, and reference the issue with
   `Closes #N`.

## What reviewers look for

- **Zero warnings** from every linter. No new `# noqa`, `# type: ignore` or lint
  disables.
- **mypy in strict mode** passes.
- Tests cover the behaviour you changed, and none are skipped, marked xfail or flaky.
- No secrets, hostnames, personal data or real trading results in the diff.
- Behaviour that touches the ledger, tax figures or risk limits needs a clear reason and
  tests for its edge cases.

Pull requests from forks run CI on GitHub-hosted runners with a read-only token. Some
checks that need the maintainer's infrastructure run after review instead.

## Licence

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same licence as the project.

## Conduct

Please follow the [code of conduct](CODE_OF_CONDUCT.md).
