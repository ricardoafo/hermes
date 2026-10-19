"""The one assertion for a validator that accepts by returning ``None``.

A check such as ``validate_http_config`` or ``check_startup`` has no state to
read afterwards: it accepts by returning and refuses by raising. A test that
only calls it passes for any return value and states no claim, so this helper
asserts the return value as well (issue #1193).
"""

from collections.abc import Callable

__all__ = ["assert_accepted"]


def assert_accepted[**P](check: Callable[P, object], /, *args: P.args, **kwargs: P.kwargs) -> None:
    """Call ``check`` and assert it accepted: it returned ``None`` and did not raise.

    Args:
        check: A validator that returns ``None`` when it accepts its input.
        *args: Positional arguments for ``check``.
        **kwargs: Keyword arguments for ``check``.

    """
    outcome = check(*args, **kwargs)
    assert [outcome] == [None], f"{getattr(check, '__name__', check)!r} returned {outcome!r}"
