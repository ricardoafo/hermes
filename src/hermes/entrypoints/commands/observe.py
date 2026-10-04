"""``hermes observe``: the 90-day shadow observation window.

Usage: ``hermes [--demo SCENARIO] observe [--json | --weekly] [--utc]``

Day N of 90, fill drift against the 30 bps gate, execution and operations
figures, and the §10 kill triggers. ``--weekly`` prints the blind weekly
report text instead, the text a pushed message will carry.

Without a demo scenario there is no live read model yet and the window starts
with the first shadow fill (#353), so the answer is a note, not an error.

Exit codes: 0 success, 2 usage error or unknown scenario.

Reference: openspec/changes/observation-dashboard/change.md.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC
from typing import Final, TextIO

from rich.console import Console

from hermes.adapters.console import UnknownScenarioError
from hermes.application.console import Surface
from hermes.application.console.observation_render import observation_to_json, render_observation
from hermes.application.console.render import MADRID, RenderContext
from hermes.application.console.weekly_report import weekly_report
from hermes.entrypoints.commands import Command
from hermes.entrypoints.console import LiveReadModelUnavailableError, resolve_read_model

__all__ = ["COMMAND", "run_observe"]

_USAGE: Final = "Usage: hermes [--demo SCENARIO] observe [--json | --weekly] [--utc]"
_NO_DATA_NOTE: Final = "The 90-day window starts with the first shadow fill (#353)."
_FLAGS: Final = frozenset({"--json", "--weekly", "--utc"})


def run_observe(
    args: list[str],
    stream: TextIO,
    *,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run ``hermes observe``.

    Args:
        args: Arguments after ``observe``.
        stream: Output stream; colour follows Rich's TTY and ``NO_COLOR`` detection.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot; defaults to empty.

    Returns:
        0 on success, 2 for a usage error or an unknown scenario.
    """
    if "-h" in args or "--help" in args:
        stream.write(_USAGE + "\n")
        return 0
    flags = set(args)
    if flags - _FLAGS or len(flags) != len(args) or {"--json", "--weekly"} <= flags:
        stream.write(f"ERROR: bad arguments. {_USAGE}\n")
        return 2
    try:
        model = resolve_read_model(demo_flag=demo_flag, env=env or {})
    except LiveReadModelUnavailableError:
        return _without_data(stream, as_json="--json" in flags)
    except UnknownScenarioError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 2
    tz = UTC if "--utc" in flags else MADRID
    window = model.observation()
    if "--weekly" in flags:
        stream.write(weekly_report(window, demo_scenario=model.demo_scenario, tz=tz))
        return 0
    console = Console(file=stream, emoji=False, markup=False, highlight=False)
    ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI, tz=tz, width=console.width)
    if "--json" in flags:
        stream.write(json.dumps(observation_to_json(window, ctx), indent=2) + "\n")
    else:
        console.print(render_observation(window, ctx))
    return 0


def _without_data(stream: TextIO, *, as_json: bool) -> int:
    if as_json:
        stream.write(json.dumps({"observation": None, "note": _NO_DATA_NOTE}, indent=2) + "\n")
    else:
        stream.write(f"No observation data yet.\n{_NO_DATA_NOTE}\n")
    return 0


def _run(argv: list[str], *, stream: TextIO, demo: str | None) -> int:
    return run_observe(argv, stream, demo_flag=demo, env=os.environ)


COMMAND = Command(
    name="observe",
    summary="The 90-day shadow window: drift vs the 30 bps gate, ops, kill triggers.",
    run=_run,
    wants_demo=True,
)
