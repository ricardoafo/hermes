"""Tests for the prod value fingerprints every other tier refuses (ADR-024 §4, #970).

Invariants protected here:

- a fingerprint is the SHA-256 of the value without surrounding whitespace;
- every tier but prod refuses a notifier, heartbeat or error-report value
  whose fingerprint is listed, naming the variable and never the value;
- prod loads with its own values, and a malformed file never stops it;
- a fingerprint counts only for the variable it is listed under;
- the file is strict: unknown variables, non-hex entries and extra keys are
  refused, so a typo cannot silently turn the check off;
- what ``render_fingerprints`` prints loads back and matches every value it
  was rendered from.
"""

import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.infrastructure.config import ConfigError, TierError, load_config
from hermes.infrastructure.config.prod_fingerprints import (
    FINGERPRINTED_ENV_VARS,
    PROD_FINGERPRINTS_FILE,
    ProdFingerprints,
    fingerprint,
    load_prod_fingerprints,
    render_fingerprints,
)

_REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"
_TOKEN = "123456:prod-bot-token-sekrit42"  # noqa: S105  # gitleaks:allow
_ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def _config_with(tmp_path: Path, document: str) -> Path:
    """Copy the shipped config tree and replace its fingerprint file with ``document``."""
    config_dir = tmp_path / "config"
    shutil.copytree(_REPO_CONFIG, config_dir)
    (config_dir / PROD_FINGERPRINTS_FILE).write_text(document, encoding="utf-8")
    return config_dir


def _listing(name: str, *values: str) -> str:
    entries = "".join(f"    - {fingerprint(value)}\n" for value in values)
    return f"fingerprints:\n  {name}:\n{entries}"


class TestFingerprint:
    """A fingerprint is a plain SHA-256 of the trimmed value."""

    def test_is_the_sha256_of_the_value(self) -> None:
        """The published SHA-256 test vector for 'abc'."""
        assert fingerprint("abc") == _ABC_SHA256

    def test_ignores_surrounding_whitespace(self) -> None:
        """An env file's trailing newline or padding does not change the fingerprint."""
        assert fingerprint("  abc\n") == _ABC_SHA256


class TestTiers:
    """Which tiers refuse a listed value."""

    @pytest.mark.parametrize("tier", ["dev", "sandbox", "qa", "staging"])
    def test_every_tier_but_prod_refuses_a_prod_value_by_name(
        self, tmp_path: Path, tier: str
    ) -> None:
        """A copied prod token stops the tier at load, named and not echoed."""
        config_dir = _config_with(tmp_path, _listing("HERMES_TELEGRAM__BOT_TOKEN", _TOKEN))

        with pytest.raises(TierError) as excinfo:
            load_config(config_dir, env={"HERMES_ENV": tier, "HERMES_TELEGRAM__BOT_TOKEN": _TOKEN})

        assert excinfo.value.problems == (
            "HERMES_TELEGRAM__BOT_TOKEN matches a prod value, which only prod may use",
        )
        assert "sekrit42" not in str(excinfo.value)

    def test_prod_loads_with_its_own_values(self, tmp_path: Path) -> None:
        """Prod is where the listed values belong."""
        config_dir = _config_with(tmp_path, _listing("HERMES_TELEGRAM__BOT_TOKEN", _TOKEN))

        config = load_config(
            config_dir, env={"HERMES_ENV": "prod", "HERMES_TELEGRAM__BOT_TOKEN": _TOKEN}
        )

        assert config.env == "prod"

    def test_prod_is_not_stopped_by_a_malformed_file(self, tmp_path: Path) -> None:
        """Prod checks against nothing, so a broken fingerprint file cannot take it down."""
        config_dir = _config_with(tmp_path, "fingerprints: [not, a, mapping]\n")

        assert load_config(config_dir, env={"HERMES_ENV": "prod"}).env == "prod"

    def test_staging_with_its_own_values_loads(self, tmp_path: Path) -> None:
        """A different token, as staging's own bot has, is not refused."""
        config_dir = _config_with(tmp_path, _listing("HERMES_TELEGRAM__BOT_TOKEN", _TOKEN))
        env = {"HERMES_ENV": "staging", "HERMES_TELEGRAM__BOT_TOKEN": "999:staging-token"}

        assert load_config(config_dir, env=env).env == "staging"

    def test_a_prod_ping_url_is_refused_in_staging(self, tmp_path: Path) -> None:
        """Heartbeat URLs are fingerprinted too: lab pings must not feed prod's monitor."""
        ping = "https://hc-ping.com/0f3c9b5e-sekrit42"
        config_dir = _config_with(tmp_path, _listing("HERMES_HEALTHCHECKS__PING_URL", ping))

        with pytest.raises(TierError) as excinfo:
            load_config(
                config_dir, env={"HERMES_ENV": "staging", "HERMES_HEALTHCHECKS__PING_URL": ping}
            )

        assert excinfo.value.problems == (
            "HERMES_HEALTHCHECKS__PING_URL matches a prod value, which only prod may use",
        )

    def test_the_shipped_file_loads_and_refuses_nothing(self) -> None:
        """The committed file is valid; until it is filled, staging loads as before."""
        env = {"HERMES_ENV": "staging", "HERMES_TELEGRAM__BOT_TOKEN": _TOKEN}

        assert load_config(_REPO_CONFIG, env=env).env == "staging"


class TestMatching:
    """A fingerprint names one variable."""

    def test_a_fingerprint_counts_only_for_its_variable(self) -> None:
        """Prod's bot token pasted into the Slack variable is not prod's Slack token."""
        prints = ProdFingerprints({"HERMES_TELEGRAM__BOT_TOKEN": frozenset({fingerprint("t")})})

        assert prints.matching({"HERMES_SLACK__BOT_TOKEN": "t"}) == []

    def test_a_blank_value_never_matches(self) -> None:
        """An empty variable holds no value, even if the empty string were listed."""
        prints = ProdFingerprints({"HERMES_SENTRY__DSN": frozenset({fingerprint("")})})

        assert prints.matching({"HERMES_SENTRY__DSN": "  "}) == []

    def test_a_rotation_may_list_old_and_new(self, tmp_path: Path) -> None:
        """Both fingerprints listed under one variable are refused."""
        (tmp_path / PROD_FINGERPRINTS_FILE).write_text(
            _listing("HERMES_SENTRY__DSN", "old-dsn", "new-dsn"), encoding="utf-8"
        )
        prints = load_prod_fingerprints(tmp_path)

        assert [
            prints.matching({"HERMES_SENTRY__DSN": value}) for value in ("old-dsn", "new-dsn")
        ] == [["HERMES_SENTRY__DSN"], ["HERMES_SENTRY__DSN"]]

    def test_matches_are_reported_in_a_stable_order(self) -> None:
        """Every match is named, in the order the variables are declared."""
        prints = ProdFingerprints(
            {name: frozenset({fingerprint(name)}) for name in FINGERPRINTED_ENV_VARS}
        )

        assert prints.matching({name: name for name in reversed(FINGERPRINTED_ENV_VARS)}) == list(
            FINGERPRINTED_ENV_VARS
        )


class TestFile:
    """The file is strict, so a typo cannot silently turn the check off."""

    def test_a_missing_file_lists_nothing(self, tmp_path: Path) -> None:
        """A hand-built config directory without the file checks no values."""
        assert load_prod_fingerprints(tmp_path).matching({"HERMES_SENTRY__DSN": "x"}) == []

    @pytest.mark.parametrize(
        ("document", "reason"),
        [
            (f"fingerprints:\n  HERMES_TELEGRAM__CHAT_ID:\n    - {_ABC_SHA256}\n", "is not a"),
            (f"fingerprints:\n  HERMES_SENTRY__DSN:\n    - {_ABC_SHA256.upper()}\n", "lowercase"),
            ("fingerprints:\n  HERMES_SENTRY__DSN:\n    - abc123\n", "lowercase"),
            (f"fingerprints:\n  HERMES_SENTRY__DSN: {_ABC_SHA256}\n", "lowercase"),
            ("fingerprints: [a, b]\n", "must map"),
            ("fingerprint: {}\n", "only top-level key"),
            ("- a\n", "only top-level key"),
            ("fingerprints: {a: [\n", "cannot read"),
        ],
    )
    def test_a_malformed_file_is_refused(self, tmp_path: Path, document: str, reason: str) -> None:
        """An unknown variable, a bad digest or a wrong shape is a config error."""
        (tmp_path / PROD_FINGERPRINTS_FILE).write_text(document, encoding="utf-8")

        with pytest.raises(ConfigError, match=reason):
            load_prod_fingerprints(tmp_path)

    def test_a_malformed_file_stops_staging(self, tmp_path: Path) -> None:
        """Outside prod the loader surfaces the broken file instead of skipping the check."""
        config_dir = _config_with(tmp_path, "fingerprints: [a]\n")

        with pytest.raises(ConfigError, match="must map variable names to lists"):
            load_config(config_dir, env={"HERMES_ENV": "staging"})


class TestRender:
    """What the fingerprint command prints is the file's own format."""

    def test_renders_only_the_set_variables_without_their_values(self) -> None:
        """Unset and blank variables are left out; values never appear."""
        env = {
            "HERMES_SENTRY__DSN": "https://k@o1.ingest.example/2",
            "HERMES_TELEGRAM__BOT_TOKEN": "abc",
            "HERMES_SLACK__BOT_TOKEN": " ",
            "HERMES_TELEGRAM__CHAT_ID": "-100",
        }

        assert render_fingerprints(env) == (
            "fingerprints:\n"
            f"  HERMES_TELEGRAM__BOT_TOKEN:\n    - {_ABC_SHA256}\n"
            f"  HERMES_SENTRY__DSN:\n    - {fingerprint('https://k@o1.ingest.example/2')}\n"
        )

    def test_renders_nothing_when_no_variable_is_set(self) -> None:
        """An env file without any fingerprinted value gives an empty document."""
        assert render_fingerprints({"HERMES_ENV": "prod"}) == ""

    @given(
        values=st.dictionaries(
            st.sampled_from(FINGERPRINTED_ENV_VARS),
            st.text(
                alphabet=st.characters(codec="utf-8", exclude_categories=("Cs", "Cc", "Zs")),
                min_size=1,
            ),
            min_size=1,
        )
    )
    def test_what_is_rendered_loads_back_and_matches(self, values: dict[str, str]) -> None:
        """Pasting the command's output into the file refuses exactly those values."""
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / PROD_FINGERPRINTS_FILE).write_text(
                render_fingerprints(values), encoding="utf-8"
            )
            prints = load_prod_fingerprints(Path(directory))

        assert prints.matching(values) == [n for n in FINGERPRINTED_ENV_VARS if n in values]
