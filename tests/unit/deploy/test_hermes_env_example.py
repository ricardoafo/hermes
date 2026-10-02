"""Invariants for ``deploy/systemd/hermes.env.example``.

The example documents the production env file. If it names a variable the
config loader rejects, an operator who copies it gets `hermes serve`
exit 3 at start (issue #306).

Reference: openspec/changes/306-serve-systemd-boot/change.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import load_config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _REPO_ROOT / "deploy" / "systemd" / "hermes.env.example"
_CONFIG_DIR = _REPO_ROOT / "config"


@pytest.fixture
def example_env() -> dict[str, str]:
    """Return only the active (uncommented) assignments of the example file."""
    return {k: v for k, v in dotenv_values(_EXAMPLE).items() if v is not None}


def test_example_env_is_accepted_by_the_real_config_loader(example_env: dict[str, str]) -> None:
    """Every active name in the example survives `load_config` validation."""
    try:
        load_config(_CONFIG_DIR, env=example_env)
    except ConfigError as exc:
        pytest.fail(f"hermes.env.example breaks config load: {exc}")


def test_example_env_keeps_mainnet_closed(example_env: dict[str, str]) -> None:
    """The MVP1 example never opens a real-money path (invariant I-08)."""
    config = load_config(_CONFIG_DIR, env=example_env)
    assert config.allow_mainnet is False


def test_example_env_selects_the_prod_tier(example_env: dict[str, str]) -> None:
    """The VPS runs the prod tier; the mainnet gate is what keeps it paper-only."""
    config = load_config(_CONFIG_DIR, env=example_env)
    assert config.env == "prod"
