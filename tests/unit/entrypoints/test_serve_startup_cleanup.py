"""A serve start that fails after an HTTP client is built closes that client (issue #1176).

Invariants:

- a ``StartupError`` raised after the FX adapter is built closes its client;
- a failure after the Telegram adapter is built closes the Telegram client;
- a start that succeeds hands every client to the composition still open.

The SQLite side of the same rule is #855's, tested in ``test_serve.py``.
"""

import asyncio
from pathlib import Path

import pytest

from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.notifier.slack_adapter import SlackAdapter
from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.domain.errors import LedgerError
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.root import HermesConfig

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TELEGRAM_ENV = {
    "HERMES_TELEGRAM__BOT_TOKEN": "123:fake",  # gitleaks:allow
    "HERMES_TELEGRAM__CHAT_ID": "1",
}


@pytest.fixture
def config() -> HermesConfig:
    """Load the shipped ``config/`` with no ambient environment."""
    return load_config(_PROJECT_ROOT / "config", env={})


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> dict[str, FrankfurterAdapter | TelegramAdapter]:
    """Record the FX and Telegram adapters the composition builds."""
    seen: dict[str, FrankfurterAdapter | TelegramAdapter] = {}
    frankfurter = serve_module._frankfurter
    telegram = TelegramAdapter.create

    def record_fx(*args: object, **kwargs: object) -> FrankfurterAdapter:
        seen["fx"] = adapter = frankfurter(*args, **kwargs)  # type: ignore[arg-type]
        return adapter

    def record_telegram(*args: object, **kwargs: object) -> TelegramAdapter:
        seen["telegram"] = adapter = telegram(*args, **kwargs)  # type: ignore[arg-type]
        return adapter

    monkeypatch.setattr(serve_module, "_frankfurter", record_fx)
    monkeypatch.setattr(serve_module.TelegramAdapter, "create", record_telegram)
    return seen


def test_a_startup_error_after_the_fx_adapter_is_built_closes_its_client(
    config: HermesConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: dict[str, FrankfurterAdapter | TelegramAdapter],
) -> None:
    """The halt-row read failing turns into StartupError and leaves the FX client closed."""

    def unreadable(*_args: object) -> str:
        raise LedgerError(msg="disk I/O error")

    monkeypatch.setattr(serve_module, "_held_shadow_kill", unreadable)

    with pytest.raises(serve_module.StartupError, match="fill-quality or halt rows"):
        serve_module.build_composition(
            config, db_path=tmp_path / "hermes.sqlite3", env={}, new_ledger=True
        )

    assert built["fx"].client.is_closed is True


def test_a_failure_after_the_telegram_adapter_is_built_closes_both_clients(
    config: HermesConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built: dict[str, FrankfurterAdapter | TelegramAdapter],
) -> None:
    """Slack failing to build closes the Telegram and FX clients built before it."""

    def broken(*_args: object, **_kwargs: object) -> SlackAdapter:
        raise RuntimeError("slack client refused")

    monkeypatch.setattr(serve_module.SlackAdapter, "create", broken)
    env = {**_TELEGRAM_ENV, "HERMES_SLACK__BOT_TOKEN": "xoxb-fake"}  # gitleaks:allow
    env["HERMES_SLACK__DEFAULT_CHANNEL"] = "C1"

    with pytest.raises(RuntimeError, match="slack client refused"):
        serve_module.build_composition(
            config, db_path=tmp_path / "hermes.sqlite3", env=env, new_ledger=True
        )

    assert (built["telegram"].client.is_closed, built["fx"].client.is_closed) == (True, True)


def test_a_successful_start_hands_over_open_clients(
    config: HermesConfig,
    tmp_path: Path,
    built: dict[str, FrankfurterAdapter | TelegramAdapter],
) -> None:
    """On success nothing is closed early: the composition owns the clients until release."""
    composition = serve_module.build_composition(
        config, db_path=tmp_path / "hermes.sqlite3", env=_TELEGRAM_ENV, new_ledger=True
    )
    try:
        assert (built["telegram"].client.is_closed, built["fx"].client.is_closed) == (
            False,
            False,
        )
    finally:
        built["telegram"].close()
        built["fx"].close()
        asyncio.run(composition.feed.aclose())
        asyncio.run(composition.orderbooks.aclose())
        composition.sqlite_connection.close()
