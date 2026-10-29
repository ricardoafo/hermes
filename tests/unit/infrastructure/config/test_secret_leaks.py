"""No credential handed to the config loader reaches an error message or a repr (#794).

Each test plants a recognisable value where a secret would be and asserts
that it never shows up in what an operator, a log line or an error report
would read. The shipped ``config/`` tree is the input, so the tests follow
whatever the repo ships.
"""

import pytest

from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import load_config
from tests.support.paths import REPO_ROOT

_CONFIG_DIR = REPO_ROOT / "config"
_PLANTED = "PLANTED-7f3a9c"


def test_binance_credentials_in_the_env_do_not_break_loading() -> None:
    """The adapter's HERMES_BINANCE__API_KEY/SECRET are not section overrides.

    Before #794 the loader fed them to the binance section, whose model
    forbids extra fields, so any process holding the credentials failed to
    load its config.
    """
    env = {
        "HERMES_BINANCE__API_KEY": f"key-{_PLANTED}",
        "HERMES_BINANCE__API_SECRET": f"secret-{_PLANTED}",
    }

    config = load_config(_CONFIG_DIR, env=env)

    assert config.binance == load_config(_CONFIG_DIR, env={}).binance


def test_a_section_validation_error_names_the_field_without_its_value() -> None:
    """A rejected override reports where and why, never the value it rejected."""
    env = {"HERMES_HEALTHCHECKS__TIMEOUT_SEC": f"not-a-number-{_PLANTED}"}

    with pytest.raises(ConfigError) as excinfo:
        load_config(_CONFIG_DIR, env=env)

    message = str(excinfo.value)
    assert (_PLANTED in message, "\ntimeout_sec\n  Input should be a valid number" in message) == (
        False,
        True,
    )


def test_a_root_validation_error_names_the_field_without_its_value() -> None:
    """An invalid tier reports the ``env`` field, never the rejected tier string."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(_CONFIG_DIR, env={"HERMES_ENV": f"qa-{_PLANTED}"})

    message = str(excinfo.value)
    expected_start = (
        "Config error: HermesConfig validation failed: 1 validation error for HermesConfig\nenv\n"
    )
    assert (_PLANTED in message, message.startswith(expected_start)) == (False, True)


@pytest.mark.parametrize(
    ("variable", "secret"),
    [
        ("HERMES_SENTRY__DSN", f"https://{_PLANTED}@o1.ingest.sentry.io/1"),
        ("HERMES_HEALTHCHECKS__PING_URL", f"https://hc-ping.com/{_PLANTED}"),
    ],
)
def test_the_config_repr_masks_the_credential_urls(variable: str, secret: str) -> None:
    """repr(HermesConfig) omits the Sentry DSN and the healthchecks ping URL."""
    config = load_config(_CONFIG_DIR, env={variable: secret})

    assert _PLANTED not in repr(config)


def test_a_masked_credential_is_still_loaded() -> None:
    """Masking the repr does not drop the value: the adapters still receive it."""
    dsn = f"https://{_PLANTED}@o1.ingest.sentry.io/1"

    config = load_config(_CONFIG_DIR, env={"HERMES_SENTRY__DSN": dsn})

    assert config.sentry.dsn == dsn
