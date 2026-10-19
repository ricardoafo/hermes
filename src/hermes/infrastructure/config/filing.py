"""Tax-filing configuration: report thresholds and the local Modelo 721 history.

Two sources:

- ``config/filing.yaml`` in the repo: the Modelo 721 threshold and increase,
  and the custodian shown on the report.
- The 721 history, outside the repo (default ``~/.config/hermes/modelo721.yaml``,
  override ``HERMES_MODELO721_HISTORY``): what the operator declared in earlier
  years. Its values are private: excluded from ``repr``, never logged, never
  copied into error messages. No file means no 721 filed yet.

Reference: openspec/archive/36-modelo-100-721/change.md D-7, D-9.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hermes.domain.filing.modelo721 import Modelo721Rules, PreviousDeclaration
from hermes.domain.value_objects.currency import Currency
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_yaml_model

__all__ = [
    "DEFAULT_MODELO721_HISTORY_PATH",
    "MODELO721_HISTORY_ENV",
    "CustodianConfig",
    "FilingConfig",
    "Modelo721History",
    "Modelo721RulesConfig",
    "load_filing_config",
    "load_modelo721_history",
    "resolve_modelo721_history_path",
]

_logger = logging.getLogger(__name__)

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_ZERO: Final[Decimal] = Decimal("0")

MODELO721_HISTORY_ENV: Final[str] = "HERMES_MODELO721_HISTORY"
DEFAULT_MODELO721_HISTORY_PATH: Final[Path] = Path("~/.config/hermes/modelo721.yaml")

_DeclaredValue = Annotated[Decimal, Field(ge=_ZERO, allow_inf_nan=False)]


class Modelo721RulesConfig(BaseModel):
    """The ``modelo721`` section.

    Attributes:
        threshold_eur: First-time threshold on the 31 December total.
        increase_eur: Increase over the last declaration that makes a new one due.
    """

    model_config = _MODEL_CONFIG

    threshold_eur: Decimal = Field(ge=_ZERO, allow_inf_nan=False)
    increase_eur: Decimal = Field(ge=_ZERO, allow_inf_nan=False)

    def to_rules(self) -> Modelo721Rules:
        """Return the domain rules."""
        return Modelo721Rules(threshold_eur=self.threshold_eur, increase_eur=self.increase_eur)


class CustodianConfig(BaseModel):
    """The ``custodian`` section.

    Attributes:
        name: Entity that holds the coins.
        country: ISO 3166-1 alpha-2 country, or ``None`` until confirmed.
    """

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class FilingConfig(BaseModel):
    """Contents of ``config/filing.yaml``."""

    model_config = _MODEL_CONFIG

    modelo721: Modelo721RulesConfig
    custodian: CustodianConfig


class Modelo721History(BaseModel):
    """Earlier Modelo 721 declarations, by tax year. Values are private.

    Attributes:
        declarations: Declared 31 December EUR value per coin, per tax year.
    """

    model_config = _MODEL_CONFIG

    declarations: dict[int, dict[Currency, _DeclaredValue]] = Field(
        default_factory=dict, repr=False
    )

    def latest_before(self, year: int) -> PreviousDeclaration | None:
        """Return the most recent declaration for a year before ``year``.

        Args:
            year: The report's tax year.

        Returns:
            That declaration, or ``None`` when none was filed before ``year``.
        """
        earlier = [y for y in self.declarations if y < year]
        if not earlier:
            return None
        latest = max(earlier)
        return PreviousDeclaration(year=latest, values_eur=dict(self.declarations[latest]))


def load_filing_config(
    config_dir: Path | None = None, *, env: dict[str, str] | None = None
) -> FilingConfig:
    """Load ``filing.yaml`` from ``config_dir``.

    Args:
        config_dir: Directory holding the YAML files; defaults to ``./config``.
        env: Optional env snapshot for ``HERMES_FILING__...`` overrides.

    Returns:
        The validated config.

    Raises:
        ConfigError: File missing, invalid YAML, or schema violation.
    """
    base = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_yaml_model(base / "filing.yaml", FilingConfig, env_section="FILING", env=env)


def resolve_modelo721_history_path(env: dict[str, str] | None = None) -> Path:
    """Return the history path: ``HERMES_MODELO721_HISTORY`` or the home default.

    Args:
        env: Optional env snapshot (defaults to ``os.environ``).

    Returns:
        The path with ``~`` expanded. The file may not exist.
    """
    snapshot = os.environ if env is None else env
    override = snapshot.get(MODELO721_HISTORY_ENV)
    path = Path(override) if override else DEFAULT_MODELO721_HISTORY_PATH
    return path.expanduser()


def load_modelo721_history(
    path: Path | None = None, *, env: dict[str, str] | None = None
) -> Modelo721History:
    """Load the 721 history; a missing file means no 721 filed yet.

    Error messages carry the file path and field locations only, never values.

    Args:
        path: Explicit path; defaults to :func:`resolve_modelo721_history_path`.
        env: Optional env snapshot (defaults to ``os.environ``).

    Returns:
        The validated history, or an empty one when the file does not exist.

    Raises:
        ConfigError: File unreadable, invalid YAML, or bad values. A present
            but malformed file fails closed.
    """
    target = path if path is not None else resolve_modelo721_history_path(env)
    if not target.is_file():
        _logger.info("modelo 721 history not found, assuming none filed")
        return Modelo721History()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read 721 history {target}: {type(exc).__name__}") from None
    except RecursionError:
        raise ConfigError(f"invalid YAML in 721 history {target}: nested too deep") from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(f"invalid YAML in 721 history {target}{where}") from None
    if raw is None:
        return Modelo721History()
    if not isinstance(raw, dict):
        raise ConfigError(f"top-level of 721 history {target} must be a mapping")
    try:
        history = Modelo721History.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid 721 history {target}: {problems}") from None
    _logger.info("modelo 721 history loaded for %d year(s)", len(history.declarations))
    return history
