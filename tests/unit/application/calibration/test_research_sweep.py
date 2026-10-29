"""Unit and property tests for research sweeps (ADR-031 §1, §6, issue #1309).

Every sweep is synthetic (``tests.support.research_sweep``); history is the
in-memory fake of the git port the batch tests use, whose subprocess adapter
has its own tests. The command-line path is covered on a real repository in
``tests/unit/entrypoints/test_batch.py``.
"""

import hashlib
import math
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.calibration.research_batch import BatchCheck, BatchError
from hermes.application.calibration.research_sweep import (
    MAX_FAMILIES,
    MAX_SWEEP_CANDIDATES,
    SWEEP_CHECK_NAMES,
    Family,
    GridAxis,
    check_sweep,
    expand,
    is_sweep,
    parse_sweep,
)
from tests.support.research_batch import BATCH_PATH, BODY, batch_text, card_path, signed
from tests.support.research_sweep import SWEEP_IDS, family, sweep_front_matter

_KNOWN = frozenset({"swing-alpha", "swing-beta"})
_DEFAULTS: dict[str, dict[str, object]] = {
    "swing-alpha": {"lookback": 20, "atr_mult": Decimal("3.0")},
    "swing-beta": {"lookback": 20},
}


def _checks(front_matter: dict[str, Any], **kwargs: Any) -> dict[str, BatchCheck]:
    text, git = signed(front_matter)
    found = check_sweep(BATCH_PATH, text, git, known_strategies=_KNOWN, **kwargs)
    return {c.name: c for c in found}


def _failed(front_matter: dict[str, Any], **kwargs: Any) -> set[str]:
    return {name for name, c in _checks(front_matter, **kwargs).items() if not c.passed}


def _refusal(front_matter: dict[str, Any]) -> BatchError:
    with pytest.raises(BatchError) as caught:
        parse_sweep(BATCH_PATH, batch_text(front_matter))
    return caught.value


# ---------------------------------------------------------------------------
# A valid sweep
# ---------------------------------------------------------------------------


def test_a_valid_signed_sweep_passes_every_check_in_order() -> None:
    """Every sweep check runs, in order, and a consistent signed sweep passes them all."""
    text, git = signed(sweep_front_matter())

    checks = check_sweep(
        BATCH_PATH,
        text,
        git,
        known_strategies=_KNOWN,
        strategy_parameters=_DEFAULTS,
        require_signed=True,
    )

    assert (tuple(c.name for c in checks), [c for c in checks if not c.passed]) == (
        SWEEP_CHECK_NAMES,
        [],
    )


def test_the_grids_expand_in_family_then_axis_order_with_the_last_axis_fastest() -> None:
    """ADR-031 §1: one fixed order, and ids of the form <family>@<param>=<value>,..."""
    sweep = parse_sweep(BATCH_PATH, batch_text(sweep_front_matter()))

    assert tuple(c.strategy_id for c in sweep.batch.candidates) == SWEEP_IDS


def test_each_point_runs_its_familys_template_at_its_grid_values() -> None:
    """A point's template and values are what the workflow runs; nothing else differs."""
    sweep = parse_sweep(BATCH_PATH, batch_text(sweep_front_matter()))

    assert (sweep.points[3].template, sweep.points[3].values) == (
        "swing-alpha",
        (("lookback", 20), ("atr_mult", Decimal("3.0"))),
    )


def test_a_points_neighbours_are_the_adjacent_values_on_its_grid() -> None:
    """The ADR-021 neighbour check moves one axis one grid step; an edge has one neighbour."""
    sweep = parse_sweep(BATCH_PATH, batch_text(sweep_front_matter()))

    first, middle = sweep.batch.candidates[0], sweep.batch.candidates[3]
    assert [(p.name, p.neighbours) for p in (*first.parameters, *middle.parameters)] == [
        ("lookback", (20,)),
        ("atr_mult", (Decimal("3.0"),)),
        ("lookback", (15, 25)),
        ("atr_mult", (Decimal("2.5"),)),
    ]


def test_every_point_of_a_family_shares_its_card_section_and_code() -> None:
    """ADR-031 §1: sizing, fees, exits and kill rules come from the family, the same for all."""
    sweep = parse_sweep(BATCH_PATH, batch_text(sweep_front_matter()))

    shared = {
        (c.idea_family, c.card, c.section, c.code_paths, c.summary) for c in sweep.batch.candidates
    }
    assert len(shared) == len(sweep.families)


def test_a_plain_batch_is_not_a_sweep() -> None:
    """ADR-026 batches keep their own checks: only kind: sweep is read as a sweep."""
    assert (is_sweep(batch_text()), is_sweep(batch_text(sweep_front_matter()))) == (False, True)


# ---------------------------------------------------------------------------
# Refusals a file shows by itself
# ---------------------------------------------------------------------------


def test_a_sweep_that_expands_past_max_candidates_is_refused() -> None:
    """ADR-031 §1: the pipeline refuses a sweep whose expansion exceeds the signed total."""
    fm = sweep_front_matter()
    fm["max_candidates"] = 7

    assert _refusal(fm).check == "candidate_cap"


@pytest.mark.parametrize("cap", [MAX_SWEEP_CANDIDATES + 1, 1, "10", True])
def test_a_max_candidates_outside_2_to_150_is_refused(cap: object) -> None:
    """ADR-031 §1: max_candidates is a signed integer of at most 150."""
    fm = sweep_front_matter()
    fm["max_candidates"] = cap

    assert _refusal(fm).check == "candidate_cap"


def test_more_than_twelve_families_are_refused() -> None:
    """ADR-031 §1: a sweep holds up to 12 idea families."""
    fm = sweep_front_matter()
    fm["families"] = [
        family(f"family-{i}", "swing-alpha", f"C{i}", {"lookback": [10, 20]})
        for i in range(MAX_FAMILIES + 1)
    ]

    assert _refusal(fm).check == "family_count"


def test_a_family_named_twice_is_refused() -> None:
    """Ids are <family>@..., so two families of one name would collide."""
    fm = sweep_front_matter()
    fm["families"][1]["family"] = "breakout"

    assert _refusal(fm).message == "families named twice: ['breakout']"


def test_a_1h_family_is_refused_until_one_hour_bars_run_everywhere() -> None:
    """ADR-031 §6: 1h families wait for issues #1237 and #1238."""
    fm = sweep_front_matter()
    fm["families"][0]["interval"] = "1h"

    refusal = _refusal(fm)
    assert (refusal.check, "#1237 and #1238" in refusal.message) == ("interval_allowed", True)


@pytest.mark.parametrize("interval", ["1d", "", None, 4])
def test_an_interval_other_than_4h_is_refused(interval: object) -> None:
    """Only the intervals ADR-031 names are allowed."""
    fm = sweep_front_matter()
    fm["families"][1]["interval"] = interval

    assert _refusal(fm).check == "interval_allowed"


def test_more_than_three_grid_axes_are_refused() -> None:
    """ADR-031 §1: the card's free-parameter cap of three still applies."""
    fm = sweep_front_matter()
    fm["families"][1]["grid"] = {"a": [1, 2], "b": [1, 2], "c": [1, 2], "d": [1, 2]}

    assert _refusal(fm).check == "parameters_cap"


@pytest.mark.parametrize(
    "values",
    [[20], [], [25, 20], [20, 20], [10, "2.5"], ["2.5", "1e1"], [1.5, 2.5], "10,20"],
)
def test_an_unusable_grid_axis_is_refused(values: object) -> None:
    """An axis lists two or more plain values of one kind, ascending and distinct."""
    fm = sweep_front_matter()
    fm["families"][1]["grid"] = {"lookback": values}

    assert _refusal(fm).check == "parameters_cap"


def test_a_sweep_with_candidates_listed_by_hand_is_refused() -> None:
    """A sweep's candidates come only from its grids."""
    fm = sweep_front_matter()
    fm["candidates"] = []

    refusal = _refusal(fm)
    assert (refusal.check, "unknown ['candidates']" in refusal.message) == ("front_matter", True)


def test_a_family_without_a_section_in_the_body_is_refused() -> None:
    """Each family's pre-registration is one section of the body."""
    fm = sweep_front_matter()
    fm["families"][1]["section"] = "C9"

    assert _refusal(fm).check == "section_present"


# ---------------------------------------------------------------------------
# Checks that read history and the roster
# ---------------------------------------------------------------------------


def test_a_template_missing_from_the_roster_fails_templates_known() -> None:
    """ADR-031 §1: each family names a template that is in code before signing."""
    fm = sweep_front_matter()
    fm["families"][1]["template"] = "swing-gamma"

    assert _failed(fm) == {"templates_known"}


def test_a_grid_axis_its_template_does_not_take_fails_templates_known() -> None:
    """The driver could not build the point, so the check refuses it before signing."""
    fm = sweep_front_matter()
    fm["families"][1]["grid"] = {"period": [10, 20]}

    assert _failed(fm, strategy_parameters=_DEFAULTS) == {"templates_known"}


def test_a_grid_of_the_wrong_kind_for_its_parameter_fails_templates_known() -> None:
    """An integer grid for a decimal parameter would change the rule, not just its value."""
    fm = sweep_front_matter()
    fm["families"][0]["grid"] = {"atr_mult": [2, 3]}

    assert _failed(fm, strategy_parameters=_DEFAULTS) == {"templates_known"}


def test_a_card_that_differs_from_its_hash_fails_naming_the_family() -> None:
    """A card is a family's, so its failure names the family, not a grid point."""
    fm = sweep_front_matter()
    fm["families"][1]["card_sha256"] = hashlib.sha256(b"other\n").hexdigest()

    detail = _checks(fm)["card_hashes"].detail
    assert detail.startswith("pullback: ")


def test_a_grid_edited_after_signing_fails_content_sha256() -> None:
    """One signature signs the whole sweep; a widened grid is not what was signed."""
    fm = sweep_front_matter()
    _, git = signed(fm)
    fm["families"][0]["grid"]["lookback"].append(30)

    checks = check_sweep(BATCH_PATH, batch_text(fm), git, known_strategies=_KNOWN)

    assert [c.name for c in checks if not c.passed] == ["content_sha256"]


def test_a_draft_fails_only_status_signed_when_a_signature_is_required() -> None:
    """The backtest workflow runs with --require-signed, so a DRAFT sweep never runs."""
    fm = sweep_front_matter()
    _, git = signed(sweep_front_matter())
    fm.update(status="DRAFT", signed_by=None, date_signed=None)
    fm.update(content_sha256=None, content_sha256_scope=None, signing_commit_precursor=None)

    checks = check_sweep(
        BATCH_PATH, batch_text(fm, BODY), git, known_strategies=_KNOWN, require_signed=True
    )

    assert [c.name for c in checks if not c.passed] == ["status_signed"]


def test_unreadable_front_matter_fails_every_check() -> None:
    """Nothing is checkable without front matter, and the operator sees every line fail."""
    _, git = signed(sweep_front_matter())

    checks = check_sweep(BATCH_PATH, "no front matter\n", git, known_strategies=_KNOWN)

    assert [c.passed for c in checks] == [False] * (len(SWEEP_CHECK_NAMES) - 1)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

_AXIS = st.lists(st.integers(min_value=1, max_value=500), min_size=2, max_size=6, unique=True)
_GRID = st.lists(_AXIS, min_size=1, max_size=3)


def _families(grids: list[list[list[int]]]) -> tuple[Family, ...]:
    return tuple(
        Family(
            family=f"f{i}",
            template="swing-alpha",
            card=card_path("swing-alpha"),
            card_sha256="0" * 64,
            section=f"C{i}",
            summary="s",
            code_paths=("src/hermes/swing_alpha.py",),
            interval="4h",
            grid=tuple(GridAxis(f"p{j}", tuple(sorted(v))) for j, v in enumerate(grid)),
        )
        for i, grid in enumerate(grids)
    )


@given(st.lists(_GRID, min_size=1, max_size=4))
def test_expansion_is_the_full_product_with_unique_ids_every_time(
    grids: list[list[list[int]]],
) -> None:
    """Same families, same ordered candidates; one per grid point and no id twice."""
    families = _families(grids)

    first, second = expand(families), expand(families)

    ids = [p.candidate_id for p in first]
    expected = sum(math.prod(len(axis) for axis in grid) for grid in grids)
    assert (first == second, len(ids), len(set(ids))) == (True, expected, expected)


@given(
    st.lists(st.integers(min_value=2, max_value=6), min_size=1, max_size=3),
    st.integers(min_value=2, max_value=MAX_SWEEP_CANDIDATES),
)
def test_a_sweep_parses_iff_its_expansion_is_within_max_candidates(
    sizes: list[int], cap: int
) -> None:
    """The signed cap holds for every grid: over it is refused, at or under it parses."""
    fm = sweep_front_matter()
    fm["max_candidates"] = cap
    fm["families"][1]["grid"] = {f"p{j}": list(range(1, n + 1)) for j, n in enumerate(sizes)}
    total = 6 + math.prod(sizes)

    try:
        parsed: int | str = len(parse_sweep(BATCH_PATH, batch_text(fm)).points)
    except BatchError as exc:
        parsed = exc.check

    assert parsed == (total if total <= cap else "candidate_cap")
