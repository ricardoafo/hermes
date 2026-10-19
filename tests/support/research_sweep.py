"""Synthetic research sweeps for the ADR-031 sweep tests.

:func:`sweep_front_matter` returns a valid two-family sweep as a plain mapping,
which a test edits before :func:`~tests.support.research_batch.batch_text`
renders it. It reuses the batch fixture's cards, code and signing helpers, so a
sweep and a batch are signed the same way. Every value is made up.
"""

import copy
import hashlib
from typing import Any

from tests.support.research_batch import CARD_BYTES, batch_front_matter, card_path

SWEEP_IDS = (
    "breakout@lookback=15,atr_mult=2.5",
    "breakout@lookback=15,atr_mult=3.0",
    "breakout@lookback=20,atr_mult=2.5",
    "breakout@lookback=20,atr_mult=3.0",
    "breakout@lookback=25,atr_mult=2.5",
    "breakout@lookback=25,atr_mult=3.0",
    "pullback@lookback=10",
    "pullback@lookback=20",
)
"""The valid sweep's candidates, in expansion order."""


def family(name: str, template: str, section: str, grid: dict[str, list[Any]]) -> dict[str, Any]:
    """One family entry whose card and code are the batch fixture's for ``template``."""
    return {
        "family": name,
        "template": template,
        "card": card_path(template),
        "card_sha256": hashlib.sha256(CARD_BYTES[template]).hexdigest(),
        "section": section,
        "summary": f"{template} enters on its signal.\nExpected sign: positive.\n",
        "code_paths": [f"src/hermes/{template.replace('-', '_')}.py"],
        "interval": "4h",
        "grid": grid,
    }


def sweep_front_matter() -> dict[str, Any]:
    """Return a fresh sweep front matter that passes every check a file shows by itself."""
    fm = batch_front_matter()
    del fm["candidates"]
    fm["kind"] = "sweep"
    fm["max_candidates"] = 10
    fm["families"] = [
        family(
            "breakout",
            "swing-alpha",
            "C1",
            {"lookback": [15, 20, 25], "atr_mult": ["2.5", "3.0"]},
        ),
        family("pullback", "swing-beta", "C2", {"lookback": [10, 20]}),
    ]
    return copy.deepcopy(fm)
