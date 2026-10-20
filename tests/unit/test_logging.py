"""Unit tests for :mod:`hermes.infrastructure.logging`.

Every test protects one rule of the logging contract in the module docstring
and ADR-008 §1: prod and staging write one JSON object per line for journald,
local tiers write the console format, every line carries an ISO-8601 UTC
timestamp, its level and the bound correlation ID, debug is filtered out, and
correlation IDs are time-ordered ULIDs.

# SEV-1: correlation IDs are the primary audit trail per ADR-008 §1.
# If binding breaks, post-mortem grep-ability is dead and every incident
# investigation regresses to "find the log by wall clock" — unreliable.

Each test configures structlog for real, logs through
``structlog.get_logger``, and reads the rendered line from stdout.
"""

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from structlog.contextvars import clear_contextvars
from ulid import ULID

from hermes.infrastructure.logging import (
    bind_correlation_id,
    configure_logging,
    new_correlation_id,
)

_CID = "01HKQMZXYZ0123456789ABCDEF"


@pytest.fixture(autouse=True)
def _restore_structlog() -> Iterator[None]:
    """Start each test with empty context; reset structlog's global config afterwards."""
    clear_contextvars()
    yield
    clear_contextvars()
    structlog.reset_defaults()


def _log_one_line(capsys: pytest.CaptureFixture[str], env: str, **fields: Any) -> str:
    configure_logging(env=env)
    structlog.get_logger("hermes.tests.logging").info("rebalance.cycle.started", **fields)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    return lines[0]


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_journald_tiers_write_one_json_object_per_line(
    capsys: pytest.CaptureFixture[str], env: str
) -> None:
    """Prod and staging render each log call as a JSON object for journald (#192)."""
    line = _log_one_line(capsys, env, pair="BTCUSDT")

    entry = json.loads(line)
    assert (entry["event"], entry["level"], entry["pair"]) == (
        "rebalance.cycle.started",
        "info",
        "BTCUSDT",
    )


@pytest.mark.parametrize("env", ["dev", "sandbox"])
def test_local_tiers_write_the_console_format(capsys: pytest.CaptureFixture[str], env: str) -> None:
    """Local tiers render a human-readable line, not JSON."""
    line = _log_one_line(capsys, env)

    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
    assert "rebalance.cycle.started" in line


def test_local_tiers_colour_the_console_output() -> None:
    """The dev console renderer emits ANSI colour codes.

    The rendered string is read from the configured chain rather than from
    stdout: on Windows, colorama strips the codes when stdout is not a TTY.
    """
    configure_logging(env="dev")
    render = structlog.get_config()["processors"][-1]

    line = render(None, "info", {"event": "rebalance.cycle.started", "level": "info"})

    assert "\x1b[" in line


# SEV-1: ADR-008 §1 correlation-ID audit trail
def test_bound_correlation_id_appears_on_the_rendered_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A correlation ID bound before the call is written on the JSON log line."""
    bind_correlation_id(_CID)

    entry = json.loads(_log_one_line(capsys, "prod"))

    assert entry["correlation_id"] == _CID


# SEV-1: ADR-008 §1 correlation-ID audit trail
def test_rebinding_replaces_the_correlation_id(capsys: pytest.CaptureFixture[str]) -> None:
    """A new cycle's ID replaces the previous one instead of keeping the stale ID."""
    bind_correlation_id("01HKQMZXYZ00000000000000AA")
    bind_correlation_id(_CID)

    entry = json.loads(_log_one_line(capsys, "prod"))

    assert entry["correlation_id"] == _CID


def test_timestamp_is_iso8601_utc_with_z_suffix(capsys: pytest.CaptureFixture[str]) -> None:
    """Timestamps are ISO-8601 in UTC, marked with the ``Z`` suffix."""
    entry = json.loads(_log_one_line(capsys, "prod"))

    assert entry["timestamp"].endswith("Z")


def test_debug_lines_are_filtered_out(capsys: pytest.CaptureFixture[str]) -> None:
    """Below-info calls write nothing; the filter level is info."""
    configure_logging(env="prod")
    log = structlog.get_logger("hermes.tests.logging")

    log.debug("noise.debug")
    log.info("signal.info")

    events = [json.loads(line)["event"] for line in capsys.readouterr().out.splitlines()]
    assert events == ["signal.info"]


def test_logged_exception_carries_the_formatted_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``log.exception`` writes the traceback text into the JSON line."""
    configure_logging(env="prod")
    log = structlog.get_logger("hermes.tests.logging")

    try:
        raise ValueError("price feed stale")
    except ValueError:
        log.exception("adapter.failed")

    entry = json.loads(capsys.readouterr().out)
    assert entry["exception"].splitlines()[-1] == "ValueError: price feed stale"


def test_new_correlation_id_is_a_ulid_stamped_with_now() -> None:
    """A new correlation ID decodes as a ULID whose timestamp is the current time."""
    before = time.time()
    cid = new_correlation_id()
    after = time.time()

    stamped = ULID.from_str(cid).timestamp
    assert before - 0.001 <= stamped <= after + 0.001
