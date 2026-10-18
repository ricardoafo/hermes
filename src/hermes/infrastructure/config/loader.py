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

A setting that breaks its tier's rules raises :class:`TierError`, a
``ConfigError`` that lists every such problem by name.

Design note: we do NOT use ``BaseSettings`` + ``YamlConfigSettingsSource``
directly. That path forces every sub-config to declare its own env prefix
and creates five parallel merge chains. Reading YAML → deep-merging env →
``Model.model_validate`` keeps one merge chain, one prefix, one error path.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from hermes.adapters.endpoint import (
    DEFAULT_SLACK_API_BASE,
    DEFAULT_TELEGRAM_API_BASE,
    SLACK_API_BASE_ENV,
    TELEGRAM_API_BASE_ENV,
    is_loopback,
)
from hermes.infrastructure.config.errors import ConfigError, TierError
from hermes.infrastructure.config.models import (
    BinanceExchangeConfig,
    FeesConfig,
    FrankfurterConfig,
    HealthchecksConfig,
    KlinesConfig,
    KrakenPublicConfig,
    MarketDataConfig,
    MLConfig,
    SentryConfig,
    StrategiesConfig,
    TaxGuardConfig,
)
from hermes.infrastructure.config.root import LOOPBACK_TIERS, EndpointTierError, HermesConfig

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_DOTENV_PATH",
    "env_tier_problems",
    "load_config",
    "load_optional_yaml_model",
    "load_sentry_config",
    "load_yaml_model",
]


DEFAULT_CONFIG_DIR: Path = Path("config")
# Local-dev secrets file. Gitignored — production reads env from a systemd
# EnvironmentFile at /etc/hermes/env.production, and CI reads from GH Actions secrets.
# See docs/decisions/2026-08-23-credential-storage-layered-by-env.md.
DEFAULT_DOTENV_PATH: Path = Path(".env.local")
_ENV_PREFIX: str = "HERMES_"
_NESTED_DELIMITER: str = "__"
# Root-level env-var names — not part of the nested HERMES_<section>__<field>
# namespace because they gate runtime tier (adapter-wiring policy), not a
# specific YAML section.
_ENV_TIER: str = "HERMES_ENV"
_ENV_ALLOW_MAINNET: str = "HERMES_ALLOW_MAINNET"
# Variables that share a section's prefix but belong to an adapter or a script, not
# to the section's YAML model (BinanceSpotConfig.from_env reads these).
# Treating them as overrides failed validation under extra=forbid and
# echoed the secret in the error (#794).
_CREDENTIAL_ENV_VARS: frozenset[str] = frozenset(
    {
        "HERMES_BINANCE__API_KEY",
        "HERMES_BINANCE__API_SECRET",
        # Read by the deploy shell scripts only; the Healthchecks YAML model has
        # no field for them, so as overrides they stopped `hermes serve` at start.
        "HERMES_HEALTHCHECKS__DAILY_PING_URL",
        "HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL",
    }
)
# Keys that can place orders. Dev and sandbox keep their testnet keys; qa
# runs on fakes and staging on public data only, so neither may hold one
# (ADR-024 §4). Prod's own gate is HERMES_ALLOW_MAINNET.
_TRADING_KEY_ENV_VARS: tuple[str, ...] = (
    "HERMES_BINANCE__API_KEY",
    "HERMES_BINANCE__API_SECRET",
    "HERMES_KRAKEN__API_KEY",
    "HERMES_KRAKEN__API_SECRET",
)
_NO_TRADING_KEY_TIERS: frozenset[str] = frozenset({"qa", "staging"})
_NOTIFIER_API_BASES: Mapping[str, str] = MappingProxyType(
    {
        TELEGRAM_API_BASE_ENV: DEFAULT_TELEGRAM_API_BASE,
        SLACK_API_BASE_ENV: DEFAULT_SLACK_API_BASE,
    }
)


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its top-level mapping.

    Args:
        path: Absolute or CWD-relative path to the YAML file.

    Returns:
        Dict parsed from the YAML document. An empty file yields ``{}``.

    Raises:
        ConfigError: File missing, unreadable, not UTF-8, non-mapping root,
            or YAML parse failure (including nesting too deep to parse).
    """
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"cannot read {path}: not valid UTF-8 (byte {exc.start}: {exc.reason})"
        ) from exc
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    except RecursionError:
        raise ConfigError(f"invalid YAML in {path}: nested too deep") from None
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(f"top-level of {path} must be a mapping, got {type(parsed).__name__}")
    # Pydantic accepts str-keyed dicts; YAML permits non-str keys but hermes
    # config files always use string keys. Normalise defensively.
    return {str(k): v for k, v in parsed.items()}


def _coerce_scalar(value: str) -> Any:
    """Coerce a string env value to bool / int / str.

    Pydantic itself is quite lenient with strict=True + string input for
    scalars: booleans need explicit true/false, numerics are parsed. We do
    a light pre-pass so ``HERMES_..._ENABLED=true`` becomes ``True`` before
    validation, matching 12-factor convention.

    Args:
        value: Raw env-var string.

    Returns:
        Bool for ``true``/``false`` (case-insensitive); int if parseable as
        int; otherwise the original string, decimals included.
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
    # A decimal string stays a string: pydantic parses it exactly into a Decimal
    # field, where a float pre-pass would round it first (#1176).
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
        if not key.startswith(prefix) or key in _CREDENTIAL_ENV_VARS:
            continue
        path = key[len(prefix) :].split(_NESTED_DELIMITER)
        # Empty path segment means the env-var was malformed (e.g. trailing
        # __). Skip it rather than crash — 12-factor is best-effort.
        if any(not part for part in path):
            continue
        _set_nested(merged, [p.lower() for p in path], _coerce_scalar(value))
    return merged


def _describe(exc: ValidationError) -> str:
    """Render ``exc`` like pydantic does, minus every input value.

    Pydantic's own text includes ``input_value=...``, which for a
    credential is the secret itself. Each line keeps the location, the
    message and the error type, which is all an operator needs (#794).
    """
    count = exc.error_count()
    noun = "error" if count == 1 else "errors"
    lines = [f"{count} validation {noun} for {exc.title}"]
    for error in exc.errors(include_input=False, include_url=False):
        lines.append(".".join(str(part) for part in error["loc"]))
        lines.append(f"  {error['msg']} [type={error['type']}]")
    return "\n".join(lines)


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
        ConfigError: File missing / unreadable / not UTF-8, YAML invalid,
            nested too deep or self-referencing, or schema validation fails.
    """
    env_snapshot = dict(os.environ) if env is None else env
    raw = _read_yaml(path)
    try:
        merged = _apply_env_overrides(raw, env_section, env_snapshot)
        return model_cls.model_validate(merged)
    except RecursionError:
        # A YAML alias can make a mapping contain itself; copying it recurses.
        raise ConfigError(f"{path} refers to itself or is nested too deep") from None
    except ValidationError as exc:
        raise ConfigError(
            f"validation failed for {path} against {model_cls.__name__}: {_describe(exc)}"
        ) from exc


def load_optional_yaml_model[T: BaseModel](
    path: Path,
    model_cls: type[T],
    *,
    env_section: str,
    env: dict[str, str] | None = None,
) -> T:
    """Load a YAML file like :func:`load_yaml_model`, but let a missing file mean defaults.

    For sections that no running code needs yet: a fresh clone or a
    deployment without the file still starts, and the model's own defaults
    (plus any ``HERMES_<section>__...`` override) apply.

    Args:
        path: Path to the YAML file; it may not exist.
        model_cls: Pydantic model to validate against; every field needs a default.
        env_section: Uppercase section name for env overrides.
        env: Optional env-var snapshot (defaults to ``os.environ``).

    Returns:
        A validated ``model_cls`` instance.

    Raises:
        ConfigError: The file exists but is unreadable or invalid, or the
            merged values fail schema validation.
    """
    env_snapshot = dict(os.environ) if env is None else env
    if path.is_file():
        return load_yaml_model(path, model_cls, env_section=env_section, env=env_snapshot)
    try:
        return model_cls.model_validate(_apply_env_overrides({}, env_section, env_snapshot))
    except ValidationError as exc:
        raise ConfigError(
            f"validation failed for {model_cls.__name__} defaults: {_describe(exc)}"
        ) from exc


def load_sentry_config(config_dir: Path, *, env: dict[str, str] | None = None) -> SentryConfig:
    """Load ``sentry.yaml`` from ``config_dir`` (optional), with ``HERMES_SENTRY__*`` overrides.

    The CLI initializes Sentry before it knows which command runs, so it
    loads only this section: a fresh clone that has not populated every
    other YAML file still gets error reporting.

    Raises:
        ConfigError: The file exists but is invalid.
    """
    return load_optional_yaml_model(
        config_dir / "sentry.yaml", SentryConfig, env_section="SENTRY", env=env
    )


def _root_env_kwargs(env: dict[str, str]) -> dict[str, Any]:
    """Extract root-tier env vars (``HERMES_ENV``, ``HERMES_ALLOW_MAINNET``).

    Returns a kwargs dict suitable for passing to :class:`HermesConfig`.
    Missing vars are omitted so the model's own defaults apply — we never
    invent a value here. Coercion for ``allow_mainnet`` reuses
    :func:`_coerce_scalar` so ``yes``/``no``/``true``/``false`` all work.

    Args:
        env: Env-var snapshot.

    Returns:
        Dict with only the keys that were present in ``env``. Kept small so
        the composite constructor call stays a single readable statement.
    """
    kwargs: dict[str, Any] = {}
    tier = env.get(_ENV_TIER)
    if tier is not None:
        kwargs["env"] = tier
    mainnet = env.get(_ENV_ALLOW_MAINNET)
    if mainnet is not None:
        kwargs["allow_mainnet"] = _coerce_scalar(mainnet)
    return kwargs


def _is_prod(env: Mapping[str, str]) -> bool:
    return env.get(_ENV_TIER, "").strip().lower() == "prod"


def load_config(
    config_dir: Path | None = None,
    *,
    env: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> HermesConfig:
    """Load every hermes YAML file under ``config_dir`` into :class:`HermesConfig`.

    Before reading any env var, ``load_config`` calls
    :func:`dotenv.load_dotenv` against ``dotenv_path`` (default:
    ``.env.local`` in CWD). Missing dotenv files are treated as no-op —
    production reads env from a systemd ``EnvironmentFile`` and never has
    ``.env.local``. When ``env`` is supplied explicitly (tests) the
    dotenv step is skipped so tests never depend on the ambient shell.

    With ``HERMES_ENV=prod`` the dotenv step is skipped and ``config_dir``
    must be absolute, so the working directory can never choose the
    config or the env a production process runs with.

    Args:
        config_dir: Directory containing the YAML files. Defaults to
            :data:`DEFAULT_CONFIG_DIR` (``./config`` relative to CWD).
        env: Optional env-var snapshot (defaults to ``os.environ``). Injected
            for tests so we never read the ambient shell. When supplied,
            ``dotenv_path`` is IGNORED — tests own their env fully.
        dotenv_path: Optional path to a dotenv file. Defaults to
            :data:`DEFAULT_DOTENV_PATH`. Only consulted when ``env`` is
            ``None``. Missing files are a silent no-op (12-factor: prod
            has no dotenv).

    Returns:
        Fully-validated composite :class:`HermesConfig`.

    Raises:
        ConfigError: Any of the YAML files is missing, malformed, or fails
            schema validation. The message identifies the offending file and
            the pydantic validation error. Also raised when the tier is prod
            and ``config_dir`` is relative.
    """
    base = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    if env is None:
        # Populate os.environ from .env.local (if it exists) BEFORE
        # snapshotting. `override=False` — a real shell env var always
        # wins over dotenv, so CI and prod are never silently shadowed
        # by a stray developer .env.local. Prod never reads one: its env
        # comes from the systemd EnvironmentFile, and a relative dotenv
        # resolves against whatever directory the process started in.
        if not _is_prod(os.environ):
            dotenv_target = dotenv_path if dotenv_path is not None else DEFAULT_DOTENV_PATH
            load_dotenv(dotenv_target, override=False)
        env_snapshot = dict(os.environ)
    else:
        env_snapshot = env
    if _is_prod(env_snapshot) and not base.is_absolute():
        raise ConfigError(
            f"config dir {str(base)!r} is relative; with HERMES_ENV=prod pass an "
            "absolute --config-dir so the working directory cannot pick the config"
        )

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
    tax_guard = load_optional_yaml_model(
        base / "tax_guard.yaml",
        TaxGuardConfig,
        env_section="TAX_GUARD",
        env=env_snapshot,
    )
    ml = load_optional_yaml_model(
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
    frankfurter = load_yaml_model(
        base / "frankfurter.yaml",
        FrankfurterConfig,
        env_section="FRANKFURTER",
        env=env_snapshot,
    )
    healthchecks = load_yaml_model(
        base / "healthchecks.yaml",
        HealthchecksConfig,
        env_section="HEALTHCHECKS",
        env=env_snapshot,
    )
    sentry = load_sentry_config(base, env=env_snapshot)
    kraken_public = load_yaml_model(
        base / "kraken_public.yaml",
        KrakenPublicConfig,
        env_section="KRAKEN_PUBLIC",
        env=env_snapshot,
    )
    market_data = load_yaml_model(
        base / "market_data.yaml",
        MarketDataConfig,
        env_section="MARKET_DATA",
        env=env_snapshot,
    )

    root_kwargs = _root_env_kwargs(env_snapshot)
    try:
        config = HermesConfig(
            **root_kwargs,
            strategies=strategies,
            binance=binance,
            fees=fees,
            tax_guard=tax_guard,
            ml=ml,
            klines=klines,
            frankfurter=frankfurter,
            healthchecks=healthchecks,
            sentry=sentry,
            kraken_public=kraken_public,
            market_data=market_data,
        )
    except ValidationError as exc:
        endpoint_error = _endpoint_tier_error(exc)
        if endpoint_error is None:
            # Root-level validation covers env-tier gates (mainnet outside prod)
            # + env literal narrowing. Wrap in ConfigError for a consistent
            # error type across the loader.
            raise ConfigError(f"HermesConfig validation failed: {_describe(exc)}") from exc
        problems = [
            *endpoint_error.problems,
            *env_tier_problems(endpoint_error.env, env_snapshot),
        ]
        raise _tier_error(endpoint_error.env, problems) from exc
    problems = env_tier_problems(config.env, env_snapshot)
    if problems:
        raise _tier_error(config.env, problems)
    return config


def _endpoint_tier_error(exc: ValidationError) -> EndpointTierError | None:
    """Return the tier refusal when it is the only thing pydantic rejected."""
    errors = exc.errors(include_input=False, include_url=False)
    if len(errors) != 1:
        return None
    cause = errors[0].get("ctx", {}).get("error")
    return cause if isinstance(cause, EndpointTierError) else None


def _tier_error(tier: str, problems: list[str]) -> TierError:
    return TierError(f"HERMES_ENV={tier}: " + "; ".join(problems), problems=tuple(problems))


def env_tier_problems(tier: str, env: Mapping[str, str]) -> list[str]:
    """Return how the env-only settings break the tier's rules (ADR-024 §4, #970).

    The YAML-backed endpoints are checked by :class:`HermesConfig`; these
    settings live only in the environment, beside their credentials. Each
    problem names the variable, never its value.

    Args:
        tier: The validated ``HERMES_ENV``.
        env: Env-var snapshot.

    Returns:
        The problems found; empty when the environment fits the tier.
    """
    problems = []
    if tier in _NO_TRADING_KEY_TIERS:
        problems += [
            f"{name} is set, but {tier} holds no trading key"
            for name in _TRADING_KEY_ENV_VARS
            if env.get(name, "").strip()
        ]
    for name, default in _NOTIFIER_API_BASES.items():
        value = env.get(name, "").strip()
        if not value:
            continue
        if tier not in LOOPBACK_TIERS and is_loopback(value):
            allowed = ", ".join(sorted(LOOPBACK_TIERS))
            problems.append(f"{name} points at a loopback host, which only {allowed} may use")
        elif tier == "prod" and value.rstrip("/") != default:
            problems.append(f"{name} is not the default endpoint, which prod requires")
    return problems
