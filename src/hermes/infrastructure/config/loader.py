"""YAML → pydantic model loader with 12-factor env-var override.

Precedence (lowest → highest):

1. YAML file content on disk.
2. ``HERMES_<SECTION>__<FIELD>`` environment variables (pydantic-settings
   nested-delimiter convention).

Env overrides are opt-in per field — you never need to duplicate the whole
YAML file in env vars just to change one knob. Nested override keys use
double-underscore as the delimiter:

    HERMES_STRATEGIES__STRATEGIES__0__WEIGHT=0.5

lifts ``strategies[0].weight`` to ``0.5`` regardless of the YAML value.

The loader raises :class:`ConfigError` on:

- missing file,
- YAML parse failure,
- pydantic validation failure (with the full validation error attached).

Design note: we do NOT use ``BaseSettings`` + ``YamlConfigSettingsSource``
directly. That path forces every sub-config to declare its own env prefix
and creates five parallel merge chains. Reading YAML → deep-merging env →
``Model.model_validate`` keeps one merge chain, one prefix, one error path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.models import (
    BinanceExchangeConfig,
    FeesConfig,
    KlinesConfig,
    MLConfig,
    StrategiesConfig,
    TaxGuardConfig,
)
from hermes.infrastructure.config.root import HermesConfig

__all__ = ["DEFAULT_CONFIG_DIR", "load_config", "load_yaml_model"]


DEFAULT_CONFIG_DIR: Path = Path("config")
_ENV_PREFIX: str = "HERMES_"
_NESTED_DELIMITER: str = "__"


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its top-level mapping.

    Args:
        path: Absolute or CWD-relative path to the YAML file.

    Returns:
        Dict parsed from the YAML document. An empty file yields ``{}``.

    Raises:
        ConfigError: File missing, unreadable, non-mapping root, or YAML
            parse failure.
    """
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(f"top-level of {path} must be a mapping, got {type(parsed).__name__}")
    # Pydantic accepts str-keyed dicts; YAML permits non-str keys but hermes
    # config files always use string keys. Normalise defensively.
    return {str(k): v for k, v in parsed.items()}


def _coerce_scalar(value: str) -> Any:
    """Coerce a string env value to bool / int / float / str.

    Pydantic itself is quite lenient with strict=True + string input for
    scalars: booleans need explicit true/false, numerics are parsed. We do
    a light pre-pass so ``HERMES_..._ENABLED=true`` becomes ``True`` before
    validation, matching 12-factor convention.

    Args:
        value: Raw env-var string.

    Returns:
        Bool for ``true``/``false`` (case-insensitive); int if parseable as
        int; float if parseable as float; otherwise the original string.
    """
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _apply_env_overrides(
    data: dict[str, Any],
    section: str,
    env: dict[str, str],
) -> dict[str, Any]:
    """Return ``data`` with matching ``HERMES_<section>__...`` env overrides applied.

    Args:
        data: Dict parsed from YAML.
        section: Uppercase section name (e.g. ``"STRATEGIES"``).
        env: Env-var snapshot (usually ``os.environ`` — passed explicitly for
            testability).

    Returns:
        Deep-merged dict. Input ``data`` is not mutated.
    """
    prefix = f"{_ENV_PREFIX}{section}{_NESTED_DELIMITER}"
    merged: dict[str, Any] = _deep_copy_dict(data)
    for key, value in env.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].split(_NESTED_DELIMITER)
        # Empty path segment means the env-var was malformed (e.g. trailing
        # __). Skip it rather than crash — 12-factor is best-effort.
        if any(not part for part in path):
            continue
        _set_nested(merged, [p.lower() for p in path], _coerce_scalar(value))
    return merged


def _deep_copy_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Shallow-recursive dict copy — enough for YAML config trees."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            out[k] = [_deep_copy_dict(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out


def _set_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    """Set ``value`` at ``path`` inside ``target``, creating intermediates."""
    cursor: Any = target
    for i, part in enumerate(path):
        is_last = i == len(path) - 1
        # Numeric segment addresses a list index (e.g. strategies__0__weight).
        if part.isdigit() and isinstance(cursor, list):
            idx = int(part)
            if idx >= len(cursor):
                # Out-of-range env override — ignore silently rather than
                # extend the list with Nones, which would fail validation.
                return
            if is_last:
                cursor[idx] = value
            else:
                cursor = cursor[idx]
            continue
        if not isinstance(cursor, dict):
            return
        if is_last:
            cursor[part] = value
            return
        next_step = cursor.get(part)
        if not isinstance(next_step, dict | list):
            next_step = {}
            cursor[part] = next_step
        cursor = next_step


def load_yaml_model[T: BaseModel](
    path: Path,
    model_cls: type[T],
    *,
    env_section: str,
    env: dict[str, str] | None = None,
) -> T:
    """Load a single YAML file, apply env overrides, and validate against ``model_cls``.

    Args:
        path: Path to the YAML file.
        model_cls: Pydantic model to validate against.
        env_section: Uppercase section name for env overrides (e.g.
            ``"STRATEGIES"`` matches ``HERMES_STRATEGIES__...``).
        env: Optional env-var snapshot (defaults to ``os.environ``).

    Returns:
        A validated ``model_cls`` instance.

    Raises:
        ConfigError: File missing / unreadable, YAML invalid, or schema
            validation fails.
    """
    env_snapshot = dict(os.environ) if env is None else env
    raw = _read_yaml(path)
    merged = _apply_env_overrides(raw, env_section, env_snapshot)
    try:
        return model_cls.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            f"validation failed for {path} against {model_cls.__name__}: {exc}"
        ) from exc


def load_config(
    config_dir: Path | None = None,
    *,
    env: dict[str, str] | None = None,
) -> HermesConfig:
    """Load every hermes YAML file under ``config_dir`` into :class:`HermesConfig`.

    Args:
        config_dir: Directory containing the five YAML files. Defaults to
            :data:`DEFAULT_CONFIG_DIR` (``./config`` relative to CWD).
        env: Optional env-var snapshot (defaults to ``os.environ``). Injected
            for tests so we never read the ambient shell.

    Returns:
        Fully-validated composite :class:`HermesConfig`.

    Raises:
        ConfigError: Any of the five files is missing, malformed, or fails
            schema validation. The message identifies the offending file and
            the pydantic validation error.
    """
    base = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    env_snapshot = dict(os.environ) if env is None else env

    strategies = load_yaml_model(
        base / "strategies.yaml",
        StrategiesConfig,
        env_section="STRATEGIES",
        env=env_snapshot,
    )
    binance = load_yaml_model(
        base / "exchanges" / "binance.yaml",
        BinanceExchangeConfig,
        env_section="BINANCE",
        env=env_snapshot,
    )
    fees = load_yaml_model(
        base / "fees.yaml",
        FeesConfig,
        env_section="FEES",
        env=env_snapshot,
    )
    tax_guard = load_yaml_model(
        base / "tax_guard.yaml",
        TaxGuardConfig,
        env_section="TAX_GUARD",
        env=env_snapshot,
    )
    ml = load_yaml_model(
        base / "ml.yaml",
        MLConfig,
        env_section="ML",
        env=env_snapshot,
    )
    klines = load_yaml_model(
        base / "klines.yaml",
        KlinesConfig,
        env_section="KLINES",
        env=env_snapshot,
    )

    return HermesConfig(
        strategies=strategies,
        binance=binance,
        fees=fees,
        tax_guard=tax_guard,
        ml=ml,
        klines=klines,
    )
