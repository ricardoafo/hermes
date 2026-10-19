"""Fingerprints of prod's notifier, heartbeat and error-report values (#970).

Staging shares a box with prod and its env file is written by hand, so a
value copied from prod's env file would send lab alerts to prod's chat,
count lab pings as prod's heartbeat and file lab errors in prod's Sentry.
``config/prod_fingerprints.yaml`` holds the SHA-256 of each prod value;
every tier but prod refuses a value whose hash is listed, naming the
variable and never the value. Neither tier ever reads the other's env file
(ADR-024 §4), and the check keeps working once the tiers move apart.

The values are long random tokens and URLs carrying them, so an unsalted
hash gives nothing away.
"""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from hermes.infrastructure.config.errors import ConfigError

__all__ = [
    "PROD_FINGERPRINTS_FILE",
    "WATCHED_VARS",
    "ProdFingerprints",
    "fingerprint",
    "load_prod_fingerprints",
    "prod_value_problems",
    "render_prod_fingerprints",
]

PROD_FINGERPRINTS_FILE: Final = "prod_fingerprints.yaml"

WatchedVar = Literal[
    "HERMES_TELEGRAM__BOT_TOKEN",
    "HERMES_SLACK__BOT_TOKEN",
    "HERMES_HEALTHCHECKS__PING_URL",
    "HERMES_HEALTHCHECKS__DAILY_PING_URL",
    "HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL",
    "HERMES_SENTRY__DSN",
]
WATCHED_VARS: Final[tuple[WatchedVar, ...]] = (
    "HERMES_TELEGRAM__BOT_TOKEN",
    "HERMES_SLACK__BOT_TOKEN",
    "HERMES_HEALTHCHECKS__PING_URL",
    "HERMES_HEALTHCHECKS__DAILY_PING_URL",
    "HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL",
    "HERMES_SENTRY__DSN",
)
"""The env vars whose prod values no other tier may hold."""

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_HEADER: Final = """\
# SHA-256 of each prod notifier, heartbeat and error-report value (#970).
#
# Every tier but prod refuses a value whose hash is listed here, so a value
# copied from prod's env file into staging's stops staging at load and at
# the install preflight. Hashes only: never put a value in this file.
#
# Regenerate on prod's machine after every rotation, and commit the output:
#   hermes config fingerprint --env-file /etc/hermes/env.production
"""


class ProdFingerprints(BaseModel):
    """The committed fingerprint file.

    Attributes:
        fingerprints: For each watched variable, the SHA-256 hex digests of
            its prod values. A list, so a rotated value can stay refused
            for a while beside its replacement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprints: dict[WatchedVar, list[Sha256Hex]] = {}

    def digests(self) -> frozenset[str]:
        """Every listed digest, whichever variable it was taken from."""
        return frozenset(digest for digests in self.fingerprints.values() for digest in digests)


def fingerprint(value: str) -> str:
    """Return the SHA-256 hex digest of ``value`` without surrounding whitespace."""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def load_prod_fingerprints(config_dir: Path) -> ProdFingerprints:
    """Read ``prod_fingerprints.yaml`` from ``config_dir``.

    No env var overrides it: an override would let the env file being
    checked switch its own check off. A missing file lists nothing, so a
    config dir built for a test still loads.

    Args:
        config_dir: The directory holding the YAML config.

    Returns:
        The parsed fingerprints; empty when the file does not exist.

    Raises:
        ConfigError: The file exists but is unreadable, not YAML, or not
            a mapping of watched variables to SHA-256 hex digests.
    """
    path = config_dir / PROD_FINGERPRINTS_FILE
    if not path.is_file():
        return ProdFingerprints()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {type(exc).__name__}") from exc
    try:
        return ProdFingerprints.model_validate(raw or {})
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) or "file"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"validation failed for {path}: {fields}") from exc


def prod_value_problems(
    tier: str, env: Mapping[str, str], fingerprints: ProdFingerprints
) -> list[str]:
    """Return one problem per watched variable that holds a prod value outside prod.

    A value is matched against every listed digest, not only its own
    variable's, so prod's daily ping URL pasted into staging's main ping
    URL is refused too.

    Args:
        tier: The validated ``HERMES_ENV``.
        env: Env-var snapshot.
        fingerprints: The committed prod fingerprints.

    Returns:
        The problems, each naming the variable and never its value.
    """
    if tier == "prod":
        return []
    digests = fingerprints.digests()
    return [
        f"{name} holds one of prod's values, which only prod may use"
        for name in WATCHED_VARS
        if (value := env.get(name, "").strip()) and fingerprint(value) in digests
    ]


def render_prod_fingerprints(env: Mapping[str, str]) -> str:
    """Render ``prod_fingerprints.yaml`` for the watched values set in ``env``.

    Args:
        env: Prod's env file, parsed.

    Returns:
        The file's text: the header comment, then the digests.
    """
    listed = {
        name: [fingerprint(value)] for name in WATCHED_VARS if (value := env.get(name, "").strip())
    }
    body = yaml.safe_dump({"fingerprints": listed}, sort_keys=False, default_flow_style=False)
    return f"{_HEADER}\n{body}"
