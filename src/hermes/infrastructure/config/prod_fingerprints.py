"""Fingerprints of prod's notifier, heartbeat and error-report values (ADR-024 §4, #970).

Until the cutover (#1085) hermes-01 holds prod's env file and a hand-written
staging env file side by side. A value copied from one into the other would
send lab alerts to prod's chat, count lab pings as prod's heartbeat and file
lab errors in prod's error-report project. The committed
``config/prod_fingerprints.yaml`` holds the SHA-256 of each such prod value,
and every tier but prod refuses a value that matches, naming the variable.
Neither tier ever reads the other's secrets, and the hash of a long random
token or URL gives the value away to no one.

The operator fills the file with ``hermes config fingerprint`` run on prod's
own env file, and again after each rotation.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

import yaml

from hermes.infrastructure.config.errors import ConfigError

__all__ = [
    "FINGERPRINTED_ENV_VARS",
    "PROD_FINGERPRINTS_FILE",
    "ProdFingerprints",
    "fingerprint",
    "load_prod_fingerprints",
    "render_fingerprints",
]

PROD_FINGERPRINTS_FILE: Final = "prod_fingerprints.yaml"
# Only long random values: the hash of a short or guessable one, such as a chat
# id, could be reversed by trying every candidate.
FINGERPRINTED_ENV_VARS: Final = (
    "HERMES_TELEGRAM__BOT_TOKEN",
    "HERMES_SLACK__BOT_TOKEN",
    "HERMES_HEALTHCHECKS__PING_URL",
    "HERMES_HEALTHCHECKS__DAILY_PING_URL",
    "HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL",
    "HERMES_SENTRY__DSN",
)
_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")


def fingerprint(value: str) -> str:
    """Return the SHA-256 of ``value`` without surrounding whitespace, in lowercase hex.

    Args:
        value: The secret, as an env file holds it.

    Returns:
        64 hex characters.
    """
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProdFingerprints:
    """The prod fingerprints of each variable.

    Attributes:
        by_name: Each fingerprinted variable's known prod fingerprints; a
            rotation may keep the old one beside the new one.
    """

    by_name: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))

    def matching(self, env: Mapping[str, str]) -> list[str]:
        """Return the variables in ``env`` whose value is one of prod's, in file order.

        Args:
            env: Env-var snapshot.

        Returns:
            The matching variable names; a blank value never matches.
        """
        return [
            name
            for name in FINGERPRINTED_ENV_VARS
            if (value := env.get(name, "").strip())
            and fingerprint(value) in self.by_name.get(name, frozenset())
        ]


def load_prod_fingerprints(config_dir: Path) -> ProdFingerprints:
    """Read ``prod_fingerprints.yaml`` from ``config_dir``.

    Not an env-overridable section: an environment that could empty the
    list would defeat the check it feeds.

    Args:
        config_dir: The release's config directory.

    Returns:
        The fingerprints; none when the file is absent, as in a test's
        hand-built config directory.

    Raises:
        ConfigError: The file is unreadable, is not a mapping of known
            variables to lists of SHA-256 hex strings, or names an unknown
            variable.
    """
    path = config_dir / PROD_FINGERPRINTS_FILE
    if not path.is_file():
        return ProdFingerprints()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {type(exc).__name__}") from exc
    document = {} if parsed is None else parsed
    if not isinstance(document, dict) or set(document) - {"fingerprints"}:
        raise ConfigError(f"{path}: the only top-level key is 'fingerprints'")
    entries = document.get("fingerprints") or {}
    if not isinstance(entries, dict):
        raise ConfigError(f"{path}: 'fingerprints' must map variable names to lists")
    by_name: dict[str, frozenset[str]] = {}
    for name, hashes in entries.items():
        if name not in FINGERPRINTED_ENV_VARS:
            raise ConfigError(f"{path}: {name!r} is not a fingerprinted variable")
        listed = hashes or []
        if not isinstance(listed, list) or not all(
            isinstance(digest, str) and _SHA256_HEX.fullmatch(digest) for digest in listed
        ):
            raise ConfigError(f"{path}: {name} must list lowercase SHA-256 hex strings")
        by_name[name] = frozenset(listed)
    return ProdFingerprints(MappingProxyType(by_name))


def render_fingerprints(env: Mapping[str, str]) -> str:
    """Render the fingerprint of each set variable in ``env`` as the file's YAML.

    Args:
        env: Prod's env file, parsed.

    Returns:
        A ``fingerprints:`` document; empty when no fingerprinted variable is set.
    """
    lines = [
        f"  {name}:\n    - {fingerprint(env[name])}\n"
        for name in FINGERPRINTED_ENV_VARS
        if env.get(name, "").strip()
    ]
    return "fingerprints:\n" + "".join(lines) if lines else ""
