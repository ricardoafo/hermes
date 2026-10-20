"""Frozen hermes errors stay catchable by their own type through any ``with`` block.

``contextlib.contextmanager`` re-raises an error with ``exc.__traceback__ = tb``.
A frozen dataclass error refused that assignment and turned into
``FrozenInstanceError``, which no ``except LedgerError`` or ``except InfraError``
caught (issue #734).

Invariant protected: every frozen ``HermesError`` in ``hermes`` raised inside a
generator context manager reaches the caller as itself, while its declared
fields stay read-only.
"""

import contextlib
import importlib
import pkgutil
from collections.abc import Iterator

import pytest

import hermes
from hermes.domain.errors import HermesError, LedgerError


def _all_subclasses(cls: type[HermesError]) -> set[type[HermesError]]:
    found: set[type[HermesError]] = set()
    for sub in cls.__subclasses__():
        found |= {sub, *_all_subclasses(sub)}
    return found


def _frozen_errors() -> list[type[HermesError]]:
    """Import every hermes module but ``__main__``; return each frozen dataclass error."""
    for module in pkgutil.walk_packages(hermes.__path__, "hermes."):
        # Importing __main__ here would make a later `python -m hermes` warn and run stale code.
        if not module.name.endswith(".__main__"):
            importlib.import_module(module.name)
    return sorted(
        (
            cls
            for cls in _all_subclasses(HermesError)
            if (params := vars(cls).get("__dataclass_params__")) is not None and params.frozen
        ),
        key=lambda cls: f"{cls.__module__}.{cls.__qualname__}",
    )


_FROZEN_ERRORS = _frozen_errors()


@contextlib.contextmanager
def _block() -> Iterator[None]:
    yield


def test_the_scan_finds_the_frozen_errors() -> None:
    """The scan sees the known frozen errors, so the parametrized test below is not empty."""
    assert LedgerError in _FROZEN_ERRORS


@pytest.mark.parametrize("error_type", _FROZEN_ERRORS, ids=lambda cls: cls.__qualname__)
def test_a_frozen_error_leaves_a_generator_context_manager_as_itself(
    error_type: type[HermesError],
) -> None:
    """Raised inside a ``@contextmanager`` block, the error is caught as itself."""
    error = error_type.__new__(error_type)
    with pytest.raises(error_type) as caught, _block():
        raise error
    assert (caught.value is error, caught.value.__traceback__ is not None) == (True, True)
