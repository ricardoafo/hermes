"""Unit tests for the pre-registration parser.

Tests cover:
- All five typed accessors return correct signed constants.
- Parser raises FileNotFoundError when the file is missing.
- Parser raises ValueError when experiment_id in frontmatter is wrong.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.application.strategy_protocol.preregistration import (
    CertificationSchedule,
    H1Thresholds,
    PreRegistration,
)
from hermes.infrastructure.project_root import load_pre_registration
from tests.support.paths import REPO_ROOT

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pre_reg(tmp_path_factory: pytest.TempPathFactory) -> PreRegistration:
    """Return a PreRegistration pointed at the real pre-reg file in the repo."""
    return load_pre_registration(REPO_ROOT)


# ---------------------------------------------------------------------------
# Accessor tests
# ---------------------------------------------------------------------------


class TestExperimentId:
    """experiment_id() returns the signed experiment identifier."""

    def test_returns_signed_id(self, pre_reg: PreRegistration) -> None:
        """experiment_id() must return 'swing-mvp1-v1' — the only signed experiment."""
        result = pre_reg.experiment_id()
        assert result == "swing-mvp1-v1", (
            f"Expected experiment_id 'swing-mvp1-v1', got '{result}'. "
            f"This is a signed constant — do not change."
        )


class TestUniverse:
    """universe() returns the signed three-symbol asset universe from §3."""

    def test_returns_three_symbols(self, pre_reg: PreRegistration) -> None:
        """universe() must return exactly 3 symbols (BTC, ETH, SOL per §3)."""
        assert pre_reg.universe() == ("BTCUSDT", "ETHUSDT", "SOLUSDT")

    def test_contains_btcusdt(self, pre_reg: PreRegistration) -> None:
        """BTCUSDT must be in the signed universe per pre-reg §3."""
        universe = pre_reg.universe()
        assert "BTCUSDT" in universe, (
            f"BTCUSDT missing from universe {universe}. Signed in pre-reg §3."
        )

    def test_contains_ethusdt(self, pre_reg: PreRegistration) -> None:
        """ETHUSDT must be in the signed universe per pre-reg §3."""
        universe = pre_reg.universe()
        assert "ETHUSDT" in universe, (
            f"ETHUSDT missing from universe {universe}. Signed in pre-reg §3."
        )

    def test_contains_solusdt(self, pre_reg: PreRegistration) -> None:
        """SOLUSDT must be in the signed universe per pre-reg §3."""
        universe = pre_reg.universe()
        assert "SOLUSDT" in universe, (
            f"SOLUSDT missing from universe {universe}. Signed in pre-reg §3."
        )


class TestDataWindows:
    """data_windows() returns the signed §4 data windows as UTC-aware datetimes."""

    def test_train_start_is_2020_09_01(self, pre_reg: PreRegistration) -> None:
        """train_start must be 2020-09-01 UTC — SOL availability constraint per §4."""
        windows = pre_reg.data_windows()
        expected = datetime(2020, 9, 1, tzinfo=UTC)
        assert windows.train_start == expected, (
            f"train_start expected {expected}, got {windows.train_start}. Signed in pre-reg §4."
        )

    def test_oos_start_is_2025_09_01(self, pre_reg: PreRegistration) -> None:
        """oos_start must be 2025-09-01 UTC — the CODE-ENFORCED read-refusal boundary."""
        windows = pre_reg.data_windows()
        expected = datetime(2025, 9, 1, tzinfo=UTC)
        assert windows.oos_start == expected, (
            f"oos_start expected {expected}, got {windows.oos_start}. "
            f"This is the CODE-ENFORCED read-refusal boundary."
        )

    def test_oos_end_is_2026_12_31(self, pre_reg: PreRegistration) -> None:
        """oos_end must be in December 2026 per pre-reg §4."""
        windows = pre_reg.data_windows()
        assert windows.oos_end.year == 2026, (
            f"oos_end year expected 2026, got {windows.oos_end.year}. Signed in pre-reg §4."
        )
        assert windows.oos_end.month == 12, (
            f"oos_end month expected 12, got {windows.oos_end.month}."
        )

    def test_all_datetimes_are_utc_aware(self, pre_reg: PreRegistration) -> None:
        """All DataWindows datetimes must be UTC-aware (never naive)."""
        windows = pre_reg.data_windows()
        fields = [
            ("train_start", windows.train_start),
            ("train_end", windows.train_end),
            ("validation_start", windows.validation_start),
            ("validation_end", windows.validation_end),
            ("oos_start", windows.oos_start),
            ("oos_end", windows.oos_end),
        ]
        for name, dt in fields:
            assert dt.tzinfo == UTC, (
                f"DataWindows.{name} must be UTC-aware (tzinfo == UTC), got {dt.tzinfo!r}."
            )


class TestH1Thresholds:
    """h1_thresholds() returns the signed §9 H1 acceptance thresholds."""

    def test_dsr_min_is_0_7(self, pre_reg: PreRegistration) -> None:
        """dsr_min must be 0.7 per pre-reg §9 primary metric."""
        thresholds = pre_reg.h1_thresholds()
        assert thresholds.dsr_min == 0.7, (
            f"dsr_min expected 0.7, got {thresholds.dsr_min}. Signed in pre-reg §9."
        )

    def test_bootstrap_ci_lower_min_is_0(self, pre_reg: PreRegistration) -> None:
        """bootstrap_ci_lower_min must be 0.0 per pre-reg §9."""
        thresholds = pre_reg.h1_thresholds()
        assert thresholds.bootstrap_ci_lower_min == 0.0, (
            f"bootstrap_ci_lower_min expected 0.0, got {thresholds.bootstrap_ci_lower_min}. "
            f"Signed in pre-reg §9."
        )

    def test_max_drawdown_max_is_15_percent(self, pre_reg: PreRegistration) -> None:
        """max_drawdown_max must be 0.15 (15%) per pre-reg §9."""
        thresholds = pre_reg.h1_thresholds()
        assert thresholds.max_drawdown_max == 0.15, (
            f"max_drawdown_max expected 0.15 (15%), got {thresholds.max_drawdown_max}. "
            f"Signed in pre-reg §9."
        )

    def test_win_rate_min_is_30_percent(self, pre_reg: PreRegistration) -> None:
        """win_rate_min must be 0.30 (30%) per pre-reg §9."""
        thresholds = pre_reg.h1_thresholds()
        assert thresholds.win_rate_min == 0.30, (
            f"win_rate_min expected 0.30 (30%), got {thresholds.win_rate_min}. "
            f"Signed in pre-reg §9."
        )

    def test_ulcer_index_max_is_10(self, pre_reg: PreRegistration) -> None:
        """ulcer_index_max must be 10.0 per operator resolution 2026-08-25."""
        thresholds = pre_reg.h1_thresholds()
        assert thresholds.ulcer_index_max == 10.0, (
            f"ulcer_index_max expected 10.0, got {thresholds.ulcer_index_max}. "
            f"Signed in pre-reg §9 (note: 10 not 8 — operator resolution 2026-08-25)."
        )


class TestKillTriggers:
    """kill_triggers() returns the signed §10 kill-trigger thresholds."""

    def test_max_drawdown_threshold_is_15_percent(self, pre_reg: PreRegistration) -> None:
        """max_drawdown_threshold must be 0.15 (15%) per pre-reg §10."""
        triggers = pre_reg.kill_triggers()
        assert triggers.max_drawdown_threshold == 0.15, (
            f"max_drawdown_threshold expected 0.15 (15%), got {triggers.max_drawdown_threshold}. "
            f"Signed in pre-reg §10."
        )

    def test_recovery_days_threshold_is_90(self, pre_reg: PreRegistration) -> None:
        """recovery_days_threshold must be 90.0 days per pre-reg §10."""
        triggers = pre_reg.kill_triggers()
        assert triggers.recovery_days_threshold == 90.0, (
            f"recovery_days_threshold expected 90.0 days, got {triggers.recovery_days_threshold}. "
            f"Signed in pre-reg §10."
        )


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


class TestPreRegistrationErrors:
    """PreRegistration raises on missing file or wrong experiment_id."""

    def test_raises_file_not_found_when_file_missing(self, tmp_path: Path) -> None:
        """FileNotFoundError raised when pre-registration file is absent."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_pre_registration(tmp_path)

    def test_raises_value_error_when_experiment_id_wrong(self, tmp_path: Path) -> None:
        """ValueError raised when frontmatter experiment_id does not match expected value."""
        pre_reg_dir = tmp_path / "docs" / "experiments" / "swing-mvp1"
        pre_reg_dir.mkdir(parents=True)
        pre_reg_file = pre_reg_dir / "pre-registration-v1.md"
        pre_reg_file.write_text(
            "---\nexperiment_id: wrong-id\nstatus: SIGNED\n---\n\n# Content\n",
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "hermes"\n')
        with pytest.raises(ValueError, match="experiment_id mismatch"):
            load_pre_registration(tmp_path)

    def test_raises_value_error_when_no_frontmatter(self, tmp_path: Path) -> None:
        """ValueError raised when the pre-registration file has no YAML frontmatter."""
        pre_reg_dir = tmp_path / "docs" / "experiments" / "swing-mvp1"
        pre_reg_dir.mkdir(parents=True)
        pre_reg_file = pre_reg_dir / "pre-registration-v1.md"
        pre_reg_file.write_text("# No frontmatter\n\ncontent here\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "hermes"\n')
        with pytest.raises(ValueError, match="no YAML frontmatter"):
            load_pre_registration(tmp_path)


class TestCertificationGateValuesMatchSignedText:
    """Every value the certification gates read is pinned to the signed text (#355)."""

    # SEV-1: real-capital gate thresholds (ADR-014) must equal the signed §9 table
    def test_all_nine_section9_rows(self, pre_reg: PreRegistration) -> None:
        """The §9 table: three primary rows and six secondary rows, exactly."""
        assert pre_reg.h1_thresholds() == H1Thresholds(
            dsr_min=0.7,
            bootstrap_ci_lower_min=0.0,
            oos_trade_count_min=30,
            max_drawdown_max=0.15,
            recovery_days_max=90.0,
            ulcer_index_max=10.0,
            win_rate_min=0.30,
            profit_factor_min=1.3,
            fees_to_gross_pnl_max=0.30,
        )

    # SEV-1: the OOS date lock and 90-day windows gate real capital (§4, §7)
    def test_certification_schedule(self, pre_reg: PreRegistration) -> None:
        """§4 discipline gate (2027-01-01) and the §7 90-day and 30 bps rules."""
        assert pre_reg.certification_schedule() == CertificationSchedule(
            certification_not_before=datetime(2027, 1, 1, tzinfo=UTC),
            shadow_min_days=90,
            shadow_drift_p50_max_bps=30.0,
            live_min_days=90,
        )
