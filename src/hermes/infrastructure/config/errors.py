"""Config-loading errors.

``ConfigError`` extends ``InfraError`` so any adapter catching an infrastructure
failure also catches config problems. Config errors surface at process start,
never at runtime — a missing file or a wrong-typed field is fail-fast so the
operator sees the problem before real capital is touched.

Reference: code-conventions.md §2 (hybrid Result / exceptions — config is
adapter/infrastructure, so exceptions are the natural shape).
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.domain.errors import InfraError

__all__ = ["ConfigError", "TierError"]


@dataclass(frozen=True, slots=True)
class ConfigError(InfraError):
    """Config file missing, unparseable, or fails schema validation.

    The message is intentionally verbose — this exception fires at process
    start and the operator needs enough context to fix the YAML file without
    a debugger. Include the file path and the underlying pydantic error text.

    Args:
        msg: Human-readable description including file path and cause.
    """

    msg: str

    def __str__(self) -> str:
        """Return the wrapped human-readable message."""
        return f"Config error: {self.msg}"


@dataclass(frozen=True, slots=True)
class TierError(ConfigError):
    """The config is well formed, but a setting breaks its tier's rules (ADR-024 §4, #970).

    Args:
        msg: Every problem on one line, after the tier.
        problems: One entry per problem. Each starts with the setting's name,
            then a space and what is wrong; none holds the setting's value.
    """

    problems: tuple[str, ...] = ()
