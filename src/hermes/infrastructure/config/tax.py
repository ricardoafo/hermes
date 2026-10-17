"""Tax configuration: per-year savings scales and the local taxpayer profile.

Pure data and loading, no tax computation (that is #395). Two sources:

- ``config/tax_scales.yaml`` in the repo: IRPF savings bands per tax year. A year
  with no scale fails closed, never a guess (ADR-016 decision 1).
- The taxpayer profile, outside the repo (default ``~/.config/hermes/taxpayer.yaml``,
  override ``HERMES_TAXPAYER_PROFILE``). Its values are private: they are excluded
  from ``repr``/``str``, never logged, and never copied into error messages
  (ADR-016 decision 12). No profile means zeros plus a notice.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_yaml_model

__all__ = [
    "DEFAULT_TAXPAYER_PROFILE_PATH",
    "NO_PROFILE_NOTICE",
    "TAXPAYER_PROFILE_ENV",
    "CarriedLoss",
    "SavingsBand",
    "SavingsScale",
    "TaxScalesConfig",
    "TaxpayerProfile",
    "TaxpayerYearProfile",
    "load_tax_scales",
    "load_taxpayer_profile",
    "resolve_taxpayer_profile_path",
]

_logger = logging.getLogger(__name__)

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

TAXPAYER_PROFILE_ENV: Final[str] = "HERMES_TAXPAYER_PROFILE"
DEFAULT_TAXPAYER_PROFILE_PATH: Final[Path] = Path("~/.config/hermes/taxpayer.yaml")
NO_PROFILE_NOTICE: Final[str] = "no taxpayer profile, assuming no other income"

_ZERO: Final[Decimal] = Decimal("0")


class SavingsBand(BaseModel):
    """One band of a savings scale.

    Attributes:
        up_to_eur: Cumulative upper limit of the band in EUR; ``None`` for the
            last band, which has no upper limit.
        rate: Marginal rate as a fraction (``0.19`` is 19%).
    """

    model_config = _MODEL_CONFIG

    up_to_eur: Decimal | None = Field(default=None, gt=_ZERO, allow_inf_nan=False)
    rate: Decimal = Field(ge=_ZERO, le=Decimal("1"), allow_inf_nan=False)


class SavingsScale(BaseModel):
    """Savings-base bands for one tax year, ordered by ascending limit.

    Attributes:
        bands: Non-empty ascending bands; only the last is unbounded, and it must be.
    """

    model_config = _MODEL_CONFIG

    bands: tuple[SavingsBand, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _bands_ascending_and_open_ended(self) -> SavingsScale:
        """Reject unordered limits and a scale without an open top band."""
        *bounded, last = self.bands
        if last.up_to_eur is not None:
            raise ValueError("last band must have up_to_eur: null (no upper limit)")
        finite = [b.up_to_eur for b in bounded if b.up_to_eur is not None]
        if len(finite) != len(bounded):
            raise ValueError("only the last band may have up_to_eur: null")
        if any(a >= b for a, b in pairwise(finite)):
            raise ValueError("band limits must be strictly ascending")
        return self


class TaxScalesConfig(BaseModel):
    """Contents of ``config/tax_scales.yaml``.

    Attributes:
        personal_minimum_eur: Default personal minimum, used when the profile
            gives none for the year.
        savings: Savings scale per tax year.
    """

    model_config = _MODEL_CONFIG

    personal_minimum_eur: Decimal = Field(ge=_ZERO, allow_inf_nan=False)
    savings: dict[int, SavingsScale] = Field(min_length=1)

    def savings_scale_for(self, year: int) -> SavingsScale:
        """Return the savings scale for ``year``.

        Args:
            year: Tax year (calendar year, Europe/Madrid).

        Returns:
            The configured scale.

        Raises:
            ConfigError: No scale is configured for ``year``.
        """
        try:
            return self.savings[year]
        except KeyError:
            raise ConfigError(f"scale missing for {year}") from None


class CarriedLoss(BaseModel):
    """A net loss carried forward from an earlier year.

    Attributes:
        origin_year: Year the loss arose.
        amount_eur: Remaining loss, as a non-negative amount.
    """

    model_config = _MODEL_CONFIG

    origin_year: int = Field(ge=2000, le=2100, repr=False)
    amount_eur: Decimal = Field(ge=_ZERO, allow_inf_nan=False, repr=False)


class TaxpayerYearProfile(BaseModel):
    """The operator's other income for one tax year. All values are private.

    Every field is excluded from ``repr``/``str``.

    Attributes:
        general_income_eur: General-base income (salary etc.).
        personal_minimum_eur: Override of the default personal minimum, or ``None``.
        other_savings_income_eur: Interest, dividends and other savings income.
        other_capital_gains_eur: Net gains (negative for a net loss) from other
            transmissions in the same tax year.
        losses_carried_forward: Losses from prior years still usable.
    """

    model_config = _MODEL_CONFIG

    general_income_eur: Decimal = Field(default=_ZERO, ge=_ZERO, allow_inf_nan=False, repr=False)
    personal_minimum_eur: Decimal | None = Field(
        default=None, ge=_ZERO, allow_inf_nan=False, repr=False
    )
    other_savings_income_eur: Decimal = Field(
        default=_ZERO, ge=_ZERO, allow_inf_nan=False, repr=False
    )
    # Signed: a same-year loss outside hermes nets against hermes gains that year.
    other_capital_gains_eur: Decimal = Field(default=_ZERO, allow_inf_nan=False, repr=False)
    losses_carried_forward: tuple[CarriedLoss, ...] = Field(default=(), repr=False)


class TaxpayerProfile(BaseModel):
    """Per-year taxpayer profile; empty when the operator has none.

    Attributes:
        years: Profile per tax year. Excluded from ``repr``/``str``.
    """

    model_config = _MODEL_CONFIG

    years: dict[int, TaxpayerYearProfile] = Field(default_factory=dict, repr=False)

    def for_year(self, year: int) -> TaxpayerYearProfile:
        """Return the profile for ``year``, or zeros when none is configured.

        Args:
            year: Tax year.

        Returns:
            The configured year profile, or an all-zero profile.
        """
        return self.years.get(year, TaxpayerYearProfile())

    def has_year(self, year: int) -> bool:
        """Return whether the operator supplied figures for ``year``."""
        return year in self.years

    def notice_for(self, year: int) -> str | None:
        """Return the notice outputs must show when ``year`` has no profile data.

        Args:
            year: Tax year.

        Returns:
            :data:`NO_PROFILE_NOTICE` when assuming zeros, else ``None``.
        """
        return None if self.has_year(year) else NO_PROFILE_NOTICE


def load_tax_scales(
    config_dir: Path | None = None, *, env: dict[str, str] | None = None
) -> TaxScalesConfig:
    """Load ``tax_scales.yaml`` from ``config_dir``.

    Args:
        config_dir: Directory holding the YAML files; defaults to ``./config``.
        env: Optional env snapshot for ``HERMES_TAX_SCALES__...`` overrides.

    Returns:
        The validated scales.

    Raises:
        ConfigError: File missing, invalid YAML, or schema violation.
    """
    base = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_yaml_model(
        base / "tax_scales.yaml", TaxScalesConfig, env_section="TAX_SCALES", env=env
    )


def resolve_taxpayer_profile_path(env: dict[str, str] | None = None) -> Path:
    """Return the profile path: ``HERMES_TAXPAYER_PROFILE`` or the home default.

    Args:
        env: Optional env snapshot (defaults to ``os.environ``).

    Returns:
        The path with ``~`` expanded. The file may not exist.
    """
    snapshot = os.environ if env is None else env
    override = snapshot.get(TAXPAYER_PROFILE_ENV)
    path = Path(override) if override else DEFAULT_TAXPAYER_PROFILE_PATH
    return path.expanduser()


def load_taxpayer_profile(
    path: Path | None = None, *, env: dict[str, str] | None = None
) -> TaxpayerProfile:
    """Load the taxpayer profile; a missing file yields an empty (all-zero) profile.

    Error messages carry the file path and field locations only, never values.

    Args:
        path: Explicit profile path; defaults to :func:`resolve_taxpayer_profile_path`.
        env: Optional env snapshot (defaults to ``os.environ``).

    Returns:
        The validated profile, or an empty one when the file does not exist.

    Raises:
        ConfigError: File unreadable, invalid YAML, or bad values. A present but
            malformed profile fails closed rather than silently assuming zeros.
    """
    target = path if path is not None else resolve_taxpayer_profile_path(env)
    if not target.is_file():
        _logger.info("taxpayer profile not found, assuming no other income")
        return TaxpayerProfile()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read taxpayer profile {target}: {type(exc).__name__}") from None
    except RecursionError:
        raise ConfigError(f"invalid YAML in taxpayer profile {target}: nested too deep") from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(f"invalid YAML in taxpayer profile {target}{where}") from None
    if raw is None:
        return TaxpayerProfile()
    if not isinstance(raw, dict):
        raise ConfigError(f"top-level of taxpayer profile {target} must be a mapping")
    try:
        profile = TaxpayerProfile.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid taxpayer profile {target}: {problems}") from None
    _logger.info("taxpayer profile loaded for %d year(s)", len(profile.years))
    return profile
