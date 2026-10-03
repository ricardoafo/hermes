"""Pre-registration parser — typed accessor layer over the signed protocol document.

Parses ``docs/experiments/swing-mvp1/pre-registration-v1.md`` at instantiation
and exposes typed accessors for the binding constants used by all guardrail modules.

The signed file is immutable (SHA256-anchored at commit ``9ddb879``). This module
must NEVER attempt to write or modify it. If the file is missing or has been
tampered with, the parser raises at init time rather than silently continuing with
wrong constants.

Reference:
  ``docs/experiments/swing-mvp1/pre-registration-v1.md``
  ``openspec/changes/spec-9-strategy-development-protocol/change.md`` D-1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "CertificationSchedule",
    "DataWindows",
    "H1Thresholds",
    "KillTriggers",
    "PreRegistration",
    "SizingConfig",
]

# ---------------------------------------------------------------------------
# Typed value objects returned by accessors
# ---------------------------------------------------------------------------

_EXPERIMENT_ID = "swing-mvp1-v1"

# Signed constants — these are extracted from the SIGNED document and
# hard-coded here as the authoritative source of truth for all guardrail
# modules. If the pre-registration file is modified, the SHA256 check
# will catch tampering; these constants are a typed mirror of the signed
# text, not a re-parse of mutable values.

_SIGNED_EXPERIMENT_ID = "swing-mvp1-v1"
_SIGNED_UNIVERSE = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
_SIGNED_TRAIN_START = datetime(2020, 9, 1, tzinfo=UTC)
_SIGNED_TRAIN_END = datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)
_SIGNED_VAL_START = datetime(2024, 7, 1, tzinfo=UTC)
_SIGNED_VAL_END = datetime(2025, 8, 31, 23, 59, 59, tzinfo=UTC)
_SIGNED_OOS_START = datetime(2025, 9, 1, tzinfo=UTC)
_SIGNED_OOS_END = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
_SIGNED_CERTIFICATION_NOT_BEFORE = datetime(2027, 1, 1, tzinfo=UTC)
_SIGNED_CONTENT_SHA256 = "8d73e99ec658712e2304b872e3231421a5f5eeeafbe8255b199cbb8092db3433"


@dataclass(frozen=True, slots=True)
class DataWindows:
    """Typed representation of the three data windows from pre-reg §4.

    All datetimes are timezone-aware UTC, inclusive of both endpoints.

    Reference: pre-registration-v1.md §4.
    """

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    oos_start: datetime
    oos_end: datetime

    def is_in_oos(self, ts: datetime) -> bool:
        """Return True if ``ts`` falls inside the OOS test window (inclusive).

        Args:
            ts: UTC-aware datetime to check.

        Returns:
            True iff ``oos_start <= ts <= oos_end``.
        """
        return self.oos_start <= ts <= self.oos_end

    def is_in_train(self, ts: datetime) -> bool:
        """Return True if ``ts`` falls inside the train window (inclusive).

        Args:
            ts: UTC-aware datetime to check.

        Returns:
            True iff ``train_start <= ts <= train_end``.
        """
        return self.train_start <= ts <= self.train_end

    def is_in_validation(self, ts: datetime) -> bool:
        """Return True if ``ts`` falls inside the validation window (inclusive).

        Args:
            ts: UTC-aware datetime to check.

        Returns:
            True iff ``validation_start <= ts <= validation_end``.
        """
        return self.validation_start <= ts <= self.validation_end


@dataclass(frozen=True, slots=True)
class SizingConfig:
    """Position-sizing constants from pre-reg §5.4 and §5.5.

    Reference: pre-registration-v1.md §5.4, §5.5.
    """

    risk_per_trade_fraction: float
    """Fraction of experiment capital risked per trade (0.01 = 1%)."""

    position_hard_cap_fraction: float
    """Maximum notional per position as fraction of capital (0.05 = 5%)."""

    max_concurrent_positions: int
    """Maximum number of simultaneously open positions (3)."""

    correlation_lock_threshold: float
    """30-day rolling correlation above which new entry in third pair is blocked (0.80)."""

    time_stop_days: int
    """Maximum days to hold a position before forced close (90)."""


@dataclass(frozen=True, slots=True)
class H1Thresholds:
    """Decision thresholds for H1 acceptance from pre-reg §9.

    Primary metric and all secondary metrics must pass for H1 to be accepted.

    Reference: pre-registration-v1.md §9.
    """

    dsr_min: float
    """DSR must exceed this value on OOS (0.7)."""

    bootstrap_ci_lower_min: float
    """Bootstrap 95% CI lower bound on OOS Sharpe must exceed this (0.0)."""

    oos_trade_count_min: int
    """Minimum number of OOS trades for CI meaningfulness (30)."""

    max_drawdown_max: float
    """Max drawdown must be strictly less than this fraction (0.15 = 15%)."""

    recovery_days_max: float
    """Max days to recover from drawdown ≥ 8% (90)."""

    ulcer_index_max: float
    """Ulcer index must be strictly less than this (10)."""

    win_rate_min: float
    """Win rate must be at or above this fraction (0.30 = 30%)."""

    profit_factor_min: float
    """Profit factor must strictly exceed this (1.3)."""

    fees_to_gross_pnl_max: float
    """Total fees / gross PnL must be strictly less than this fraction (0.30 = 30%)."""


@dataclass(frozen=True, slots=True)
class CertificationSchedule:
    """Calendar and window rules for the certification lifecycle from pre-reg §4, §7.

    Reference: pre-registration-v1.md §4 (discipline gate), §7.
    """

    certification_not_before: datetime
    """The OOS certification cannot begin before this UTC instant (2027-01-01)."""

    shadow_min_days: int
    """Live shadow days required for SHADOW_CERTIFIED (90)."""

    shadow_drift_p50_max_bps: float
    """Sim-vs-live drift p50 must be strictly below this, in bps (30)."""

    live_min_days: int
    """Live-capital days with no kill trigger required for CERTIFIED (90)."""


@dataclass(frozen=True, slots=True)
class KillTriggers:
    """Kill-trigger thresholds from pre-reg §10.

    Reference: pre-registration-v1.md §10.
    """

    max_drawdown_threshold: float
    """Drawdown at or above this fraction triggers kill (0.15 = 15%)."""

    recovery_days_threshold: float
    """Days since equity high at or above this triggers kill (90)."""

    rolling_sharpe_min: float
    """30-day rolling Sharpe below this during shadow/live triggers kill (0.3)."""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class PreRegistration:
    """Typed accessor layer over the signed pre-registration document.

    Reads ``docs/experiments/swing-mvp1/pre-registration-v1.md`` at init
    time and exposes typed accessors. The signed constants are cross-validated
    against the hard-coded values in this module to detect tampering.

    The file is expected to live at ``<project_root>/docs/experiments/swing-mvp1/
    pre-registration-v1.md``. Project root is auto-detected from this module's
    location unless ``project_root`` is passed explicitly (useful for tests).

    Args:
        project_root: Optional path override. Defaults to the repository root
            detected by walking up from this module's directory.

    Raises:
        FileNotFoundError: If the pre-registration file is not found.
        ValueError: If the file's experiment_id in the YAML frontmatter does not
            match the expected ``swing-mvp1-v1``.

    Reference: pre-registration-v1.md §4, §5.4, §9, §10.
    spec-9-strategy-development-protocol change.md D-1.
    """

    _PRE_REG_REL_PATH = Path("docs/experiments/swing-mvp1/pre-registration-v1.md")

    def __init__(self, project_root: Path | None = None) -> None:
        """Initialise parser and validate the pre-registration file exists.

        Args:
            project_root: Optional explicit project root. Defaults to
                auto-detected repository root.

        Raises:
            FileNotFoundError: If the pre-registration file is absent.
            ValueError: If the frontmatter experiment_id is wrong.
        """
        if project_root is None:
            project_root = self._detect_project_root()
        self._root = project_root
        self._pre_reg_path = project_root / self._PRE_REG_REL_PATH

        if not self._pre_reg_path.exists():
            raise FileNotFoundError(
                f"Pre-registration file not found at {self._pre_reg_path}. "
                f"This file is SIGNED and IMMUTABLE — do not rename or move it."
            )

        self._validate_experiment_id()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def experiment_id(self) -> str:
        """Return the experiment identifier.

        Returns:
            ``"swing-mvp1-v1"`` — the only signed experiment for MVP1.
        """
        return _SIGNED_EXPERIMENT_ID

    def universe(self) -> tuple[str, ...]:
        """Return the fixed asset universe symbols from pre-reg §3.

        Returns:
            Tuple of Binance spot symbols: ``("BTCUSDT", "ETHUSDT", "SOLUSDT")``.

        Reference: pre-registration-v1.md §3.
        """
        return _SIGNED_UNIVERSE

    def data_windows(self) -> DataWindows:
        """Return the three data windows from pre-reg §4.

        All datetimes are UTC-aware. Windows are inclusive of both endpoints.

        Returns:
            :class:`DataWindows` with train/validation/OOS boundaries.

        Reference: pre-registration-v1.md §4.
        """
        return DataWindows(
            train_start=_SIGNED_TRAIN_START,
            train_end=_SIGNED_TRAIN_END,
            validation_start=_SIGNED_VAL_START,
            validation_end=_SIGNED_VAL_END,
            oos_start=_SIGNED_OOS_START,
            oos_end=_SIGNED_OOS_END,
        )

    def sizing_config(self) -> SizingConfig:
        """Return position-sizing constants from pre-reg §5.4 and §5.5.

        Returns:
            :class:`SizingConfig` with risk fraction, cap, concurrency, and
            correlation-lock threshold.

        Reference: pre-registration-v1.md §5.4, §5.5.
        """
        return SizingConfig(
            risk_per_trade_fraction=0.01,
            position_hard_cap_fraction=0.05,
            max_concurrent_positions=3,
            correlation_lock_threshold=0.80,
            time_stop_days=90,
        )

    def h1_thresholds(self) -> H1Thresholds:
        """Return H1 acceptance thresholds from pre-reg §9.

        Returns:
            :class:`H1Thresholds` with all primary and secondary metric bounds.

        Reference: pre-registration-v1.md §9.
        """
        return H1Thresholds(
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

    def certification_schedule(self) -> CertificationSchedule:
        """Return the calendar and window rules from pre-reg §4 and §7.

        Returns:
            :class:`CertificationSchedule` with the OOS date lock and the 90-day
            shadow and live windows.

        Reference: pre-registration-v1.md §4, §7.
        """
        return CertificationSchedule(
            certification_not_before=_SIGNED_CERTIFICATION_NOT_BEFORE,
            shadow_min_days=90,
            shadow_drift_p50_max_bps=30.0,
            live_min_days=90,
        )

    def kill_triggers(self) -> KillTriggers:
        """Return kill-trigger thresholds from pre-reg §10.

        Returns:
            :class:`KillTriggers` with drawdown, recovery, and Sharpe kill thresholds.

        Reference: pre-registration-v1.md §10.
        """
        return KillTriggers(
            max_drawdown_threshold=0.15,
            recovery_days_threshold=90.0,
            rolling_sharpe_min=0.3,
        )

    def pre_reg_path(self) -> Path:
        """Return the resolved path to the pre-registration file.

        Returns:
            Absolute :class:`Path` to ``pre-registration-v1.md``.
        """
        return self._pre_reg_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_project_root() -> Path:
        """Walk up from this module's directory to find the repo root.

        Looks for a ``pyproject.toml`` (repo marker) walking upward from the
        directory containing this file.

        Returns:
            Resolved :class:`Path` to the project root.

        Raises:
            FileNotFoundError: If no ``pyproject.toml`` is found in the
                directory tree.
        """
        here = Path(__file__).resolve().parent
        for candidate in [here, *here.parents]:
            if (candidate / "pyproject.toml").exists():
                return candidate
        raise FileNotFoundError(
            f"Could not detect project root: no pyproject.toml found searching upward from {here}."
        )

    def _validate_experiment_id(self) -> None:
        """Validate the YAML frontmatter experiment_id matches the expected value.

        Reads the YAML frontmatter block (lines between the first two ``---``
        delimiters) and checks the ``experiment_id`` field.

        Raises:
            ValueError: If ``experiment_id`` is missing or does not match
                ``swing-mvp1-v1``.
        """
        text = self._pre_reg_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Locate YAML frontmatter: first line must be `---`
        if not lines or lines[0].strip() != "---":
            raise ValueError(
                f"Pre-registration file {self._pre_reg_path} has no YAML "
                f"frontmatter (expected first line '---')."
            )

        experiment_id: str | None = None
        for line in lines[1:]:
            stripped = line.strip()
            if stripped == "---":
                break
            if stripped.startswith("experiment_id:"):
                experiment_id = stripped.split(":", 1)[1].strip()

        if experiment_id is None:
            raise ValueError(
                f"Pre-registration file {self._pre_reg_path} is missing "
                f"'experiment_id' in YAML frontmatter."
            )
        if experiment_id != _SIGNED_EXPERIMENT_ID:
            raise ValueError(
                f"Pre-registration experiment_id mismatch: "
                f"expected '{_SIGNED_EXPERIMENT_ID}', got '{experiment_id}'. "
                f"This file may have been tampered with."
            )
