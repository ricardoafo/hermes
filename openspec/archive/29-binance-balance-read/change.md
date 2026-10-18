# Change: 29-binance-balance-read

**SDD change**: `29-binance-balance-read`
**Date**: 2026-10-17
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/29-binance-balance-read/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes reconcile` can compare the ledger with the real Binance spot account.
A new read-only adapter implements the existing `BalanceReadPort` with one
signed `GET /api/v3/account` and nothing else, using a dedicated key with the
"reading" permission only. The reconcile entrypoint builds it only when
`allow_mainnet` is set (prod tier) and the month is still open, and refuses to run without the key rather
than falling back to the ledger-only check. The main risk is credential
handling: the key lives in two GitHub `prod` secrets that vps-bootstrap
renders into the env file (D-2, decided 17 Oct 2026).

## Scope

### In

- `src/hermes/adapters/exchanges/binance_balance_reader.py`:
  `BinanceBalanceReaderConfig` and `BinanceBalanceReader`.
- `src/hermes/adapters/exchanges/binance_spot.py`: `MAINNET_HOSTS`,
  `TESTNET_HOST` and `parse_decimal` made public for reuse; behaviour
  unchanged.
- `src/hermes/entrypoints/reconcile.py`: injectable
  `balance_reader_factory`, wired behind `allow_mainnet`.
- Unit and contract tests (respx, recorded-shape fixture
  `tests/fixtures/binance_spot/account.json`), a Hypothesis property test.
- `deploy/systemd/hermes.env.example`: the two variables, commented out.

### Out

- Provisioning the key on the VPS and in the GitHub `prod` environment
  (D-2: the Code Owner adds the secrets; the two-line vps-bootstrap change
  is its own workflow PR).
- Any order, cancel or other write endpoint; the trading adapter is
  untouched.
- Balances at a past month end: Binance reports current balances, so the
  exchange view is compared for the current month only (existing D-5 of
  `35-monthly-reconciliation`).

## Decisions

### D-1: A dedicated read-only key, separate from the trading key

- **Decision**: The reader uses its own key, created on Binance with the
  "Enable Reading" permission only and restricted to the VPS IP, read from
  `HERMES_BINANCE_READ__API_KEY` / `HERMES_BINANCE_READ__API_SECRET`.
- **Alternatives**: Reuse the trading key (rejected: a leak from a
  read-only tool would expose order rights); fall back to the trading key
  when the read key is missing (rejected: silent privilege widening).
- **Rationale**: Least privilege; ADR-003 §00 I-08 and AGENTS.md rule 7.
  A blank or missing credential raises; there is no fallback.
- **Cost of reversal**: LOW — the variable names are read in one place.

### D-2: Where the key is stored on the VPS

- **Decision**: GitHub `prod`-environment secrets
  `HERMES_BINANCE_READ__API_KEY` and `HERMES_BINANCE_READ__API_SECRET`,
  rendered into `/etc/hermes/env.production` (root:root 0600) by
  vps-bootstrap. Chosen by the Code Owner on 17 Oct 2026 (option A on
  issue #29).
- **Rejected**: a hand-written root-only file on the VPS (a second secret
  path to maintain), and reusing the trading key (D-1).
- **Consequence**: the bootstrap workflow passes the two secrets; that
  change ships as its own workflow PR. Agents never create or touch the
  secrets.
- **Cost of reversal**: LOW.

### D-3: Read only behind `allow_mainnet`; a missing key is exit 3

- **Decision**: Without `allow_mainnet` nothing is built and no env var is
  read (shadow behaviour unchanged). With it, a month still in progress
  builds the reader, and a missing key or an exchange failure prints
  `ERROR:` and exits 3. An ended month (the default, previous-month run)
  never reads the venue, so it builds nothing and needs no key.
- **Alternatives**: Skip the exchange view with a warning (rejected: an
  operator would read a CLEAN verdict that never looked at the account).
- **Rationale**: `allow_mainnet` is the existing real-capital switch, valid
  only in prod; reconciliation of real money must not degrade silently.
- **Cost of reversal**: LOW.

### D-4: Reuse the trading adapter's signing and host allow-list

- **Decision**: Import `sign`, `MAINNET_HOSTS`, `TESTNET_HOST` and
  `parse_decimal` from `binance_spot.py` instead of copying them.
- **Alternatives**: Subclass or wrap `BinanceSpotAdapter` (rejected: it
  would carry the order methods onto the read key).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Read-only surface

- **A-1.1**: The reader's public names are exactly `balances`, `create`,
  `close` and its fields; one `balances` call sends one `GET
  /api/v3/account` and no POST or DELETE
  (`tests/unit/adapters/exchanges/test_binance_balance_reader.py`).

### R-2: Credentials refused when absent, never echoed

- **A-2.1**: Blank key or secret, plain HTTP and an undocumented host raise
  `ValueError`; `from_env` names the missing variables and no value.
- **A-2.2**: Refusal and transport errors carry neither the secret nor the
  signature; the secret is not in `repr`.

### R-3: Balances are free plus locked

- **A-3.1**: Each requested asset reads free + locked, an asset not held
  reads zero, only requested assets are returned (example + property test).

### R-4: Entrypoint wiring

- **A-4.1**: With `allow_mainnet`, exchange checks appear in the report and
  the reader is closed; a missing key or exchange failure exits 3 with an
  `ERROR:` line naming both variables (key case); without it the factory is
  never called (`tests/unit/entrypoints/test_reconcile.py`).

## Design

```python
@dataclass(frozen=True, slots=True)
class BinanceBalanceReaderConfig:
    api_key: str
    api_secret: str = field(repr=False)
    base_url: str = "https://api.binance.com"  # documented spot host or testnet
    recv_window_ms: int = 5000
    timeout_sec: float = 10.0

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, base_url: str = ...) -> Self: ...


@dataclass(slots=True)
class BinanceBalanceReader:  # BalanceReadPort
    config: BinanceBalanceReaderConfig
    client: httpx.Client
    clock_ms: Callable[[], int]

    @classmethod
    def create(cls, config) -> Self: ...
    def balances(self, assets: Iterable[Currency]) -> Mapping[Currency, Decimal]: ...
    def close(self) -> None: ...
```

Errors: transport → `NetworkError` (path and exception kind only); HTTP
≥ 400 → `ExchangeError` with Binance's code and text; non-JSON, missing
`balances`, malformed row or non-numeric amount → `ExchangeError`.

`reconcile.main(..., balance_reader_factory=None)`: when
`config.allow_mainnet`, `factory(env)` (or `os.environ` when `env` is `None`); `ValueError` →
exit 3; the reader is passed as `exchange=` to `reconcile_month` and
closed in `finally`; an `InfraError` other than `LedgerError` → exit 3.

The loader's `_CREDENTIAL_ENV_VARS` is unchanged: no `BINANCE_READ` config
section exists, so `HERMES_BINANCE_READ__*` never matches an override
prefix (`HERMES_BINANCE__` requires the double underscore right after
`BINANCE`).

## Test plan

1. Every acceptance criterion above passes.
2. `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src tests`,
   `uv run lint-imports` clean.
3. `uv run pytest tests/unit` and `tests/integration` green; no network.

Test tiers touched: unit, property, contract

## Risks

- **A key with trading rights is configured as the read key**: the code
  cannot see key permissions. Mitigation: D-1 runbook (reading only, IP
  restricted); the reader has no write method to misuse it.
- **The trading-key env vars collide**: `HERMES_BINANCE__*` would be read
  as overrides of the `BINANCE` section; the new names use `BINANCE_READ`
  so they are never parsed as config.
- **Error text leaking the signed URL**: httpx exception text may contain
  the request URL; only the exception class name is kept, and chaining is
  suppressed.

## Dependencies on other changes

- Builds on `35-monthly-reconciliation` (`BalanceReadPort`, D-5).
- External: creating the key on Binance and adding the two `prod`
  secrets, by the Code Owner (D-2); the vps-bootstrap workflow PR.
