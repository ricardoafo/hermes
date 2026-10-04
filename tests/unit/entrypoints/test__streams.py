"""Tests for the shared error-stream helper (#543).

Invariants protected here:

- ``resolve_err`` returns the injected stream untouched, so a caller that
  hands a command an error stream sees every ERROR line there;
- without an injected stream it resolves the *current* ``sys.stderr``, so a
  redirected stderr is honoured and stdout stays free of ERROR lines.
"""

from __future__ import annotations

import io
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.entrypoints._streams import resolve_err


def test_an_injected_stream_is_returned_as_is() -> None:
    """Invariant: the caller's error stream, not stderr, receives ERROR lines."""
    injected = io.StringIO()

    assert resolve_err(injected) is injected


def test_the_default_is_the_stderr_of_the_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: stderr is read at call time, so a redirect after import is honoured."""
    replacement = io.StringIO()
    monkeypatch.setattr(sys, "stderr", replacement)

    assert resolve_err(None) is replacement


@given(message=st.text())
def test_whatever_is_written_lands_in_the_injected_stream(message: str) -> None:
    """Invariant: writing through the resolved stream reaches the injected one verbatim."""
    injected = io.StringIO()

    resolve_err(injected).write(message)

    assert injected.getvalue() == message
