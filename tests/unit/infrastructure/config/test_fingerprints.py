"""Tests for refusing prod's notifier, heartbeat and error-report values outside prod (#970).

Invariants protected here:

- every tier but prod refuses a watched variable whose value hashes to a
  listed fingerprint, through ``load_config`` as a ``TierError``;
- prod itself loads with its own values;
- a value is matched against every listed fingerprint, not only its own
  variable's;
- a value not listed, or a blank one, is never refused;
- the refusal names the variable and never echoes the value;
- the fingerprint file takes no env override, and a malformed one stops
  the load without echoing its content;
- a fingerprint is the SHA-256 of the value with surrounding whitespace
  removed;
- the rendered file round-trips: loading it refuses exactly the values it
  was rendered from;
- the shipped file lists no digest yet and parses.
"""

import hashlib
import shutil
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.infrastructure.config import ConfigError, TierError, load_config
from hermes.infrastructure.config.fingerprints import (
    PROD_FINGERPRINTS_FILE,
    WATCHED_VARS,
    ProdFingerprints,
    fingerprint,
    load_prod_fingerprints,
    prod_value_problems,
    render_prod_fingerprints,
)
from tests.support.paths import REPO_ROOT

_REPO_CONFIG = REPO_ROOT / "config"
_PROD_BOT = "123456789:prod-bot-token-sekrit42"  # gitleaks:allow
_PING = "https://hc-ping.com/prod-uuid-sekrit42"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Copy the shipped config and list prod's bot token and ping URL in its fingerprints."""
    target = tmp_path / "config"
    shutil.copytree(_REPO_CONFIG, target)
    (target / PROD_FINGERPRINTS_FILE).write_text(
        render_prod_fingerprints(
            {"HERMES_TELEGRAM__BOT_TOKEN": _PROD_BOT, "HERMES_HEALTHCHECKS__PING_URL": _PING}
        ),
        encoding="utf-8",
    )
    return target


def _refusal(config_dir: Path, env: dict[str, str]) -> TierError:
    with pytest.raises(TierError) as excinfo:
        load_config(config_dir, env=env)
    return excinfo.value


def test_a_fingerprint_is_the_sha256_of_the_stripped_value() -> None:
    """The digest is SHA-256 hex of the UTF-8 value, ignoring surrounding whitespace."""
    expected = hashlib.sha256(b"abc").hexdigest()

    assert (fingerprint("abc"), fingerprint("  abc\n")) == (expected, expected)
    assert expected == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize("tier", ["dev", "sandbox", "qa", "staging"])
def test_every_tier_but_prod_refuses_a_prod_value_by_name(config_dir: Path, tier: str) -> None:
    """A copied prod bot token stops every non-prod tier, naming the variable."""
    error = _refusal(config_dir, {"HERMES_ENV": tier, "HERMES_TELEGRAM__BOT_TOKEN": _PROD_BOT})

    assert error.problems == (
        "HERMES_TELEGRAM__BOT_TOKEN holds one of prod's values, which only prod may use",
    )


def test_prod_loads_with_its_own_values(config_dir: Path) -> None:
    """Prod holds the listed values legitimately, so its load is unaffected."""
    env = {"HERMES_ENV": "prod", "HERMES_TELEGRAM__BOT_TOKEN": _PROD_BOT}

    assert load_config(config_dir, env=env).env == "prod"


def test_a_prod_value_in_another_watched_variable_is_refused(config_dir: Path) -> None:
    """Prod's ping URL pasted into the daily ping variable is still refused."""
    error = _refusal(
        config_dir, {"HERMES_ENV": "staging", "HERMES_HEALTHCHECKS__DAILY_PING_URL": _PING}
    )

    assert error.problems == (
        "HERMES_HEALTHCHECKS__DAILY_PING_URL holds one of prod's values, which only prod may use",
    )


def test_the_refusal_never_echoes_the_value(config_dir: Path) -> None:
    """Neither the message nor the problems carry any part of the secret."""
    error = _refusal(
        config_dir,
        {
            "HERMES_ENV": "staging",
            "HERMES_TELEGRAM__BOT_TOKEN": f" {_PROD_BOT} ",
            "HERMES_HEALTHCHECKS__PING_URL": _PING,
        },
    )

    assert "sekrit42" not in str(error)
    assert len(error.problems) == 2


def test_staging_loads_with_its_own_values(config_dir: Path) -> None:
    """Values whose hashes are not listed pass."""
    env = {
        "HERMES_ENV": "staging",
        "HERMES_TELEGRAM__BOT_TOKEN": "987654321:lab-bot-token",  # gitleaks:allow
        "HERMES_HEALTHCHECKS__PING_URL": "https://hc-ping.com/lab-uuid",
    }

    assert load_config(config_dir, env=env).env == "staging"


def test_a_prod_value_is_reported_beside_a_tier_endpoint_problem(config_dir: Path) -> None:
    """When an endpoint also breaks the tier, both problems come out in one error."""
    error = _refusal(
        config_dir,
        {
            "HERMES_ENV": "staging",
            "HERMES_FRANKFURTER__BASE_URL": "http://127.0.0.1:9000",
            "HERMES_TELEGRAM__BOT_TOKEN": _PROD_BOT,
        },
    )

    assert [problem.split(" ")[0] for problem in error.problems] == [
        "frankfurter.base_url",
        "HERMES_TELEGRAM__BOT_TOKEN",
    ]


def test_the_fingerprint_file_takes_no_env_override(config_dir: Path) -> None:
    """An env var shaped like an override cannot empty the list it is checked against."""
    env = {
        "HERMES_ENV": "staging",
        "HERMES_TELEGRAM__BOT_TOKEN": _PROD_BOT,
        "HERMES_PROD_FINGERPRINTS__FINGERPRINTS": "{}",
    }

    assert _refusal(config_dir, env).problems[0].startswith("HERMES_TELEGRAM__BOT_TOKEN ")


def test_a_missing_fingerprint_file_lists_nothing(tmp_path: Path) -> None:
    """A config dir without the file loads its fingerprints as empty."""
    assert load_prod_fingerprints(tmp_path) == ProdFingerprints()


@pytest.mark.parametrize(
    "content",
    [
        "fingerprints:\n  HERMES_TELEGRAM__BOT_TOKEN: [not-a-digest-sekrit42]\n",
        f"fingerprints:\n  HERMES_TELEGRAM__CHAT_ID: [{'a' * 64}]\n",
        f"fingerprints:\n  HERMES_SENTRY__DSN: ['{'A' * 64}']\n",
        "extra: 1\n",
    ],
)
def test_a_malformed_fingerprint_file_stops_the_load(config_dir: Path, content: str) -> None:
    """A bad digest, an unwatched variable or an unknown key is a ConfigError without the value."""
    (config_dir / PROD_FINGERPRINTS_FILE).write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir, env={"HERMES_ENV": "staging"})

    assert PROD_FINGERPRINTS_FILE in str(excinfo.value)
    assert "sekrit42" not in str(excinfo.value)


def test_unparseable_fingerprint_yaml_stops_the_load(config_dir: Path) -> None:
    """Broken YAML is a ConfigError naming the file."""
    (config_dir / PROD_FINGERPRINTS_FILE).write_text("fingerprints: [\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=PROD_FINGERPRINTS_FILE):
        load_config(config_dir, env={"HERMES_ENV": "staging"})


def test_the_shipped_file_parses_and_lists_no_digest_yet() -> None:
    """The committed file is valid; the operator fills it on prod's machine."""
    assert load_prod_fingerprints(_REPO_CONFIG) == ProdFingerprints(fingerprints={})


def test_render_lists_only_the_watched_values_that_are_set() -> None:
    """Unset, blank and unwatched variables are left out of the rendered file."""
    text = render_prod_fingerprints(
        {
            "HERMES_TELEGRAM__BOT_TOKEN": _PROD_BOT,
            "HERMES_SLACK__BOT_TOKEN": "  ",
            "HERMES_TELEGRAM__CHAT_ID": "-100123",
        }
    )

    assert text.endswith(
        f"fingerprints:\n  HERMES_TELEGRAM__BOT_TOKEN:\n  - {fingerprint(_PROD_BOT)}\n"
    )
    assert _PROD_BOT not in text
    assert "-100123" not in text


_values = st.text(min_size=1, max_size=40).filter(lambda value: value.strip() != "")


@given(
    listed=st.dictionaries(st.sampled_from(WATCHED_VARS), _values, max_size=len(WATCHED_VARS)),
    staged=st.dictionaries(st.sampled_from(WATCHED_VARS), _values, max_size=len(WATCHED_VARS)),
)
def test_refused_exactly_when_the_value_was_rendered_from_prod(
    tmp_path_factory: pytest.TempPathFactory,
    listed: dict[str, str],
    staged: dict[str, str],
) -> None:
    """Round trip: a staged variable is refused iff its stripped value is one of prod's."""
    directory = tmp_path_factory.mktemp("fp")
    (directory / PROD_FINGERPRINTS_FILE).write_text(
        render_prod_fingerprints(listed), encoding="utf-8"
    )
    prod_values = {value.strip() for value in listed.values()}

    problems = prod_value_problems("staging", staged, load_prod_fingerprints(directory))

    assert [problem.split(" ")[0] for problem in problems] == [
        name for name in WATCHED_VARS if name in staged and staged[name].strip() in prod_values
    ]
