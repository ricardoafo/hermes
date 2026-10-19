"""Certification mode — the switch that lets a certification run read OOS data.

:func:`certification_context` sets a context variable for the duration of a
``with`` block, and :func:`is_certification_mode` reads it. The OOS read wall
itself is :func:`hermes.application.strategy_protocol.pinned_dataset.assert_oos_access_allowed`,
which refuses an OOS read unless this mode is on (and the lifecycle allows it).

This is part of the code-enforced read refusal mandated by
``docs/experiments/swing-mvp1/pre-registration-v1.md`` §4 discipline gate:

    "OOS Test: Touched EXACTLY ONCE at certification. Code physically refuses to
    read this window outside certification mode."

Reference:
  pre-registration-v1.md §4.
  spec-9-strategy-development-protocol change.md D-2.
"""

from contextvars import ContextVar, Token

__all__ = [
    "certification_context",
    "is_certification_mode",
]

# ---------------------------------------------------------------------------
# Context variable for certification mode
# ---------------------------------------------------------------------------

_certification_mode: ContextVar[bool] = ContextVar("hermes_certification_mode", default=False)


class _CertificationContext:
    """Class-based so frozen-dataclass errors raised inside the block propagate unchanged.

    ``contextlib.contextmanager`` assigns ``__traceback__`` on the exception it
    re-raises, which fails with ``FrozenInstanceError`` for the frozen, slotted
    :class:`~hermes.domain.errors.DomainError` subclasses.
    """

    def __init__(self) -> None:
        self._token: Token[bool] | None = None

    def __enter__(self) -> None:
        self._token = _certification_mode.set(True)

    def __exit__(self, *_exc_info: object) -> None:
        if self._token is not None:
            _certification_mode.reset(self._token)
            self._token = None


def certification_context() -> _CertificationContext:
    """Context manager that enables OOS data access for certification.

    Sets the ``hermes_certification_mode`` context variable to ``True`` for the
    duration of the ``with`` block. Inside it, :func:`is_certification_mode`
    returns ``True``, which is one of the conditions the pinned-dataset OOS
    wall requires before it allows an OOS read.

    This context manager MUST only be used by the certification harness.
    Any other use constitutes a pre-registration deviation.

    Example::

        with certification_context():
            run_certification()

    Reference: pre-registration-v1.md §4, spec-9 change.md D-2.
    """
    return _CertificationContext()


def is_certification_mode() -> bool:
    """Return True if currently inside a :func:`certification_context` block.

    Returns:
        The current value of the ``hermes_certification_mode`` context variable.
    """
    return _certification_mode.get()
