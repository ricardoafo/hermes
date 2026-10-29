"""Unit and property tests for research batches (ADR-026 §1, issue #1057).

Every batch is synthetic (``tests.support.research_batch``); history is an
in-memory fake of the git port, whose subprocess adapter has its own tests.
"""

import hashlib
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.backtest.funnel_replay import COST_STRESS
from hermes.application.calibration.batch_floors import SIGNED_BEFORE_CAP_ROW
from hermes.application.calibration.research_batch import (
    BATCH_CHECK_NAMES,
    FLOORS,
    BatchCheck,
    BatchError,
    check_batch,
    parse_batch,
    sha256_scope,
)
from tests.support.paths import REPO_ROOT
from tests.support.research_batch import (
    BATCH_PATH,
    BODY,
    CODE_COMMIT,
    PRECURSOR,
    FakeGit,
    batch_front_matter,
    batch_text,
    signed,
)

_KNOWN = frozenset({"swing-alpha", "swing-beta", "swing-gamma"})


def _checks(text: str, git: FakeGit, **kwargs: Any) -> dict[str, BatchCheck]:
    found = check_batch(BATCH_PATH, text, git, known_strategies=_KNOWN, **kwargs)
    return {c.name: c for c in found}


def _failed(text: str, git: FakeGit, **kwargs: Any) -> set[str]:
    return {name for name, c in _checks(text, git, **kwargs).items() if not c.passed}


def _refusal(front_matter: dict[str, Any], body: str = BODY) -> BatchError:
    with pytest.raises(BatchError) as caught:
        parse_batch(BATCH_PATH, batch_text(front_matter, body))
    return caught.value


# ---------------------------------------------------------------------------
# A valid batch
# ---------------------------------------------------------------------------


def test_a_valid_signed_batch_passes_every_check_in_order() -> None:
    """Every check runs, in the contract's order, and a consistent signed batch passes them all."""
    text, git = signed(batch_front_matter())

    checks = check_batch(BATCH_PATH, text, git, known_strategies=_KNOWN, require_signed=True)

    assert (tuple(c.name for c in checks), [c for c in checks if not c.passed]) == (
        BATCH_CHECK_NAMES,
        [],
    )


def test_status_signed_runs_only_when_a_signature_is_required() -> None:
    """Without --require-signed the status_signed line is left out, as the contract says."""
    text, git = signed(batch_front_matter())

    names = tuple(c.name for c in check_batch(BATCH_PATH, text, git, known_strategies=_KNOWN))

    assert names == tuple(n for n in BATCH_CHECK_NAMES if n != "status_signed")


def test_parse_returns_candidates_in_document_order_with_typed_parameters() -> None:
    """Candidates keep run order; integers stay integers and quoted decimals become Decimal."""
    batch = parse_batch(BATCH_PATH, batch_text())

    first = batch.candidates[0]
    assert (
        [c.strategy_id for c in batch.candidates],
        [(p.name, p.base, p.neighbours) for p in first.parameters],
        batch.funnel.dsr_min,
        batch.selection.fdr_q,
        batch.trial_log,
    ) == (
        ["swing-alpha", "swing-beta"],
        [
            ("lookback", 20, (15, 25)),
            ("atr_mult", Decimal("3.0"), (Decimal("2.5"), Decimal("3.5"))),
        ],
        0.95,
        0.05,
        "docs/experiments/lab/trial-log.jsonl",
    )


def test_funnel_replays_are_base_then_neighbours_in_order_then_the_cost_stress() -> None:
    """Contract §1: base, each neighbour in parameter then value order, then cost_stress=2."""
    candidate = parse_batch(BATCH_PATH, batch_text()).candidates[0]

    replays = candidate.funnel_replays()

    assert [(r.trial_id, dict(r.overrides), r.cost_multiplier) for r in replays] == [
        ("base", {}, 1),
        ("lookback=15", {"lookback": 15}, 1),
        ("lookback=25", {"lookback": 25}, 1),
        ("atr_mult=2.5", {"atr_mult": Decimal("2.5")}, 1),
        ("atr_mult=3.5", {"atr_mult": Decimal("3.5")}, 1),
        (COST_STRESS.trial_id, {}, 2),
    ]


def test_neighbour_trial_ids_are_the_neighbour_replays() -> None:
    """The ids the funnel evidence keys its neighbour series by."""
    candidate = parse_batch(BATCH_PATH, batch_text()).candidates[0]

    assert candidate.neighbour_trial_ids() == (
        "lookback=15",
        "lookback=25",
        "atr_mult=2.5",
        "atr_mult=3.5",
    )


def test_candidate_lookup_by_strategy_id() -> None:
    """A batch finds a candidate by id and answers None for an id it does not name."""
    batch = parse_batch(BATCH_PATH, batch_text())

    assert (batch.candidate("swing-beta") is batch.candidates[1], batch.candidate("swing-x")) == (
        True,
        None,
    )


# ---------------------------------------------------------------------------
# Refusals a file shows by itself (issue #1057 "Done when": one test each)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 7])
def test_fewer_than_two_or_more_than_six_candidates_are_refused(count: int) -> None:
    """ADR-026 §1: a batch holds 2 to 6 candidates."""
    fm = batch_front_matter()
    base = fm["candidates"][0]
    fm["candidates"] = [
        {**base, "strategy_id": f"swing-c{i}", "idea_family": f"family-{i}"} for i in range(count)
    ]

    assert _refusal(fm).check == "candidate_count"


def test_two_candidates_in_one_family_are_refused() -> None:
    """ADR-026 §1: one candidate per idea family per batch."""
    fm = batch_front_matter()
    fm["candidates"][1]["idea_family"] = fm["candidates"][0]["idea_family"]

    error = _refusal(fm)

    assert (error.check, "breakout" in error.message) == ("one_per_family", True)


@pytest.mark.parametrize("summary", [None, "", "   \n"])
def test_a_candidate_without_a_summary_is_refused(summary: str | None) -> None:
    """The console's Lab screen (issue #1056) shows each candidate's summary."""
    fm = batch_front_matter()
    fm["candidates"][1]["summary"] = summary

    assert _refusal(fm).check == "summary_present"


def test_a_summary_longer_than_six_lines_is_refused() -> None:
    """Contract §1: a summary is 1 to 6 lines."""
    fm = batch_front_matter()
    fm["candidates"][0]["summary"] = "\n".join(f"line {i}" for i in range(7))

    assert _refusal(fm).check == "summary_present"


def test_a_candidate_whose_section_is_not_in_the_body_is_refused() -> None:
    """Each candidate's pre-registration must be a section of the signed body."""
    error = _refusal(batch_front_matter(), body=BODY.replace("## C2", "## X2"))

    assert error.check == "section_present"


def test_a_section_heading_named_twice_is_refused() -> None:
    """Two C1 sections would leave the candidate's rules ambiguous."""
    error = _refusal(batch_front_matter(), body=BODY + "\n## C1 — again\n")

    assert error.check == "section_present"


@pytest.mark.parametrize(
    ("key", "loose"),
    [("dsr_min", 0.9), ("pbo_max", 0.1), ("t_stat_above", 2.5), ("trade_count_min", 99)],
)
def test_a_funnel_looser_than_an_adr_021_floor_is_refused(key: str, loose: float) -> None:
    """ADR-026 §1: a batch cannot loosen DSR 0.95, PBO 0.05, t 3 or 100 trades."""
    fm = batch_front_matter()
    fm["backtest_funnel"][key] = loose

    error = _refusal(fm)

    assert (error.check, key in error.message) == ("funnel_floors", True)


def test_a_funnel_without_dsr_min_is_refused() -> None:
    """The generic gate (issue #1058) reads the DSR floor from the batch, so it must be stated."""
    fm = batch_front_matter()
    del fm["backtest_funnel"]["dsr_min"]

    assert _refusal(fm).check == "funnel_floors"


def test_a_funnel_key_out_of_range_is_refused_as_pre_registrations_are() -> None:
    """The v2 keys keep their range checks (here an odd CSCV block count)."""
    fm = batch_front_matter()
    fm["backtest_funnel"]["cscv_blocks"] = 15

    error = _refusal(fm)

    assert (error.check, "cscv_blocks" in error.message) == ("funnel_floors", True)


def test_more_than_three_free_parameters_are_refused() -> None:
    """ADR-021 stage 1: a card frees at most three parameters."""
    fm = batch_front_matter()
    fm["candidates"][0]["parameters"] |= {
        "a": {"base": 1, "neighbours": [2]},
        "b": {"base": 1, "neighbours": [2]},
    }

    assert _refusal(fm).check == "parameters_cap"


@pytest.mark.parametrize(
    "spec",
    [
        {"base": 2.5, "neighbours": [2.0]},
        {"base": 20, "neighbours": [20]},
        {"base": 20, "neighbours": ["15"]},
        {"base": 20, "neighbours": []},
        {"base": 20, "neighbours": [15, 15]},
        {"base": "x", "neighbours": ["1"]},
        {"base": 20},
    ],
    ids=["float", "equal-base", "mixed-kind", "no-neighbour", "repeat", "not-number", "no-key"],
)
def test_an_unusable_parameter_is_refused(spec: dict[str, Any]) -> None:
    """Decimals are quoted strings and a neighbour differs from the base (contract §1)."""
    fm = batch_front_matter()
    fm["candidates"][0]["parameters"] = {"lookback": spec}

    assert _refusal(fm).check == "parameters_cap"


@pytest.mark.parametrize(
    ("key", "value"),
    [("rule", "best_train"), ("fdr_q", 0.1), ("fdr_q", 0), ("tie_break", "first")],
)
def test_a_selection_rule_other_than_adr_026_is_refused(key: str, value: object) -> None:
    """The champion rule is fixed by ADR-026 §5; only q is free, within (0, 0.05]."""
    fm = batch_front_matter()
    fm["selection"][key] = value

    assert _refusal(fm).check == "selection"


@pytest.mark.parametrize("missing", ["batch_id", "selection", "code_commit"])
def test_a_missing_top_level_key_is_refused(missing: str) -> None:
    """Every contract key is present, signed or not."""
    fm = batch_front_matter()
    del fm[missing]

    assert _refusal(fm).check == "front_matter"


def test_an_unknown_key_is_refused() -> None:
    """A misspelt key would otherwise be signed and ignored."""
    fm = batch_front_matter()
    fm["candidates"][0]["sumary"] = "typo"

    assert _refusal(fm).check == "front_matter"


def test_a_batch_whose_path_does_not_match_its_id_is_refused() -> None:
    """The file sits at docs/experiments/<experiment>/batches/<batch_id>.md."""
    fm = batch_front_matter()
    fm["batch_id"] = "b2"

    assert _refusal(fm).check == "front_matter"


def test_a_strategy_named_twice_is_refused() -> None:
    """Two candidates with one id would share trial lines and verdicts."""
    fm = batch_front_matter()
    fm["candidates"][1]["strategy_id"] = "swing-alpha"

    assert _refusal(fm).check == "front_matter"


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.md", "docs//x.md"])
def test_a_code_path_outside_the_repository_is_refused(path: str) -> None:
    """Paths are plain repo-relative, so git reads nothing outside the clone."""
    fm = batch_front_matter()
    fm["candidates"][0]["code_paths"] = [path]

    assert _refusal(fm).check == "front_matter"


def test_text_without_front_matter_is_refused() -> None:
    """A file that is not a batch fails front_matter and every other check as not checkable."""
    checks = check_batch(BATCH_PATH, "# no front matter\n", FakeGit(), known_strategies=_KNOWN)

    assert ([c.passed for c in checks], checks[1].detail.startswith("not checkable")) == (
        [False] * len(checks),
        True,
    )


# ---------------------------------------------------------------------------
# Refusals that read history
# ---------------------------------------------------------------------------


def test_a_mismatched_content_sha256_fails() -> None:
    """The signed hash must be the hash of the batch at its precursor commit."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm["content_sha256"] = "f" * 64

    assert _failed(batch_text(fm), git) == {"content_sha256"}


def test_a_missing_content_sha256_on_a_signed_batch_fails() -> None:
    """A SIGNED batch without its hash cannot be verified."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm["content_sha256"] = None

    assert "content_sha256" in _failed(batch_text(fm), git)


def test_a_scope_that_is_not_the_canonical_command_fails() -> None:
    """The scope names this file at the precursor, so the hash cannot point at another file."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm["content_sha256_scope"] = f"git show {PRECURSOR}:other.md | sha256sum"

    assert "content_sha256" in _failed(batch_text(fm), git)


def test_a_precursor_missing_from_history_fails() -> None:
    """A precursor the clone does not hold cannot be hashed."""
    fm = batch_front_matter()
    text, git = signed(fm)
    del git.files[PRECURSOR]

    assert _failed(text, git) == {"content_sha256"}


def test_a_parameter_edited_after_signing_fails() -> None:
    """The signature covers the precursor: a rule changed after it is a rule nobody signed."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm["candidates"][0]["parameters"]["lookback"]["neighbours"] = [10, 30]

    checks = _checks(batch_text(fm), git)
    assert (checks["content_sha256"].passed, checks["content_sha256"].detail) == (
        False,
        "changed since the signed precursor: candidates",
    )


def test_a_body_edited_after_signing_fails() -> None:
    """Each candidate's pre-registration is in the body, so the body is signed too."""
    fm = batch_front_matter()
    _, git = signed(fm)

    checks = _checks(batch_text(fm, BODY.replace("Rules.", "Looser rules.", 1)), git)
    assert (checks["content_sha256"].passed, checks["content_sha256"].detail) == (
        False,
        "changed since the signed precursor: body",
    )


def test_the_signing_commit_may_change_only_the_signature_keys() -> None:
    """Signing sets status, signer, date, hash, scope, precursor and code commit; that passes."""
    fm = batch_front_matter()
    text, git = signed(fm)

    assert _failed(text, git) == set()


def test_a_card_that_differs_from_its_hash_fails() -> None:
    """ADR-026 §2: every candidate's card hash matches its committed card."""
    text, git = signed(batch_front_matter())
    git.files["HEAD"]["docs/experiments/lab/cards/swing-beta.md"] = b"# Card: edited\n"

    checks = _checks(text, git)

    assert (checks["card_hashes"].passed, "swing-beta" in checks["card_hashes"].detail) == (
        False,
        True,
    )


def test_a_card_that_is_not_committed_fails() -> None:
    """A hash over a card nobody can read binds nothing."""
    text, git = signed(batch_front_matter())
    del git.files["HEAD"]["docs/experiments/lab/cards/swing-alpha.md"]

    assert _failed(text, git) == {"card_hashes"}


def test_code_that_differs_from_the_bound_commit_fails() -> None:
    """ADR-026 §1: the pipeline refuses a candidate whose code differs from the bound commit."""
    text, git = signed(batch_front_matter())
    git.files["HEAD"]["src/hermes/swing_alpha.py"] = b"changed after signing\n"

    checks = _checks(text, git)

    assert (checks["code_paths"].passed, "swing-alpha" in checks["code_paths"].detail) == (
        False,
        True,
    )


def test_a_code_path_missing_at_the_bound_commit_fails() -> None:
    """A path that did not exist at code_commit was not reviewed with the batch."""
    text, git = signed(batch_front_matter())
    del git.files[CODE_COMMIT]["src/hermes/swing_beta.py"]

    assert _failed(text, git) == {"code_paths"}


def test_a_code_commit_missing_from_history_fails() -> None:
    """A commit outside main's history binds nothing."""
    text, git = signed(batch_front_matter())
    del git.files[CODE_COMMIT]

    assert _failed(text, git) == {"code_paths"}


def test_a_strategy_missing_from_the_roster_fails() -> None:
    """Contract §1: each strategy id matches a config/strategies.yaml name."""
    text, git = signed(batch_front_matter())

    failed = {
        c.name
        for c in check_batch(BATCH_PATH, text, git, known_strategies={"swing-alpha"})
        if not c.passed
    }

    assert failed == {"strategy_ids_known"}


_DEFAULTS: dict[str, dict[str, object]] = {
    name: {"lookback": 20, "atr_mult": Decimal("3.0"), "fixed_period": 14} for name in _KNOWN
}


def test_parameters_matching_their_strategy_defaults_pass() -> None:
    """Each signed parameter is one the strategy takes, at its base value."""
    text, git = signed(batch_front_matter())

    assert _failed(text, git, strategy_parameters=_DEFAULTS) == set()


def test_a_parameter_the_strategy_does_not_take_fails() -> None:
    """A misspelt parameter fails before signing, not on the runner after it."""
    text, git = signed(batch_front_matter())
    defaults = {**_DEFAULTS, "swing-beta": {"look_back": 20, "atr_mult": Decimal("3.0")}}

    checks = _checks(text, git, strategy_parameters=defaults)

    assert not checks["strategy_ids_known"].passed
    assert checks["strategy_ids_known"].detail == (
        "swing-beta: its strategy takes no parameter lookback"
    )


def test_a_base_that_is_not_the_strategy_default_fails() -> None:
    """The run starts from the code's defaults, so the signed base must equal them."""
    text, git = signed(batch_front_matter())
    defaults = {**_DEFAULTS, "swing-alpha": {"lookback": 20, "atr_mult": Decimal("2.5")}}

    checks = _checks(text, git, strategy_parameters=defaults)

    assert not checks["strategy_ids_known"].passed
    assert checks["strategy_ids_known"].detail == (
        "swing-alpha: atr_mult defaults to 2.5 in the strategy, 3.0 in the batch"
    )


def test_a_draft_fails_only_status_signed_when_a_signature_is_required() -> None:
    """The workflow passes --require-signed; a DRAFT never starts a run."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm.update(status="DRAFT", signed_by=None, date_signed=None, content_sha256=None)

    assert _failed(batch_text(fm), git, require_signed=True) == {"status_signed"}


def test_an_unhashed_draft_passes_without_a_signature_required() -> None:
    """A batch's own PR checks its draft before the Code Owner signs it."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm.update(
        status="DRAFT",
        signed_by=None,
        date_signed=None,
        content_sha256=None,
        content_sha256_scope=None,
        signing_commit_precursor=None,
        code_commit=None,
    )

    checks = _checks(batch_text(fm), git)

    assert (
        [n for n, c in checks.items() if not c.passed],
        checks["content_sha256"].detail,
        checks["code_paths"].detail,
    ) == ([], "draft: not hashed yet", "draft: no code_commit yet")


def test_a_signed_batch_without_its_signer_fails_status_signed() -> None:
    """A SIGNED status with no signer is not a signature."""
    fm = batch_front_matter()
    _, git = signed(fm)
    fm["signed_by"] = ""

    assert "status_signed" in _failed(batch_text(fm), git, require_signed=True)


def test_sha256_scope_is_the_v2_signing_command() -> None:
    """The same command pre-registration v2 recorded, for this file and precursor."""
    assert sha256_scope("9d62a3d", BATCH_PATH) == (f"git show 9d62a3d:{BATCH_PATH} | sha256sum")


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def test_the_batch_template_parses_once_its_placeholders_are_filled() -> None:
    """docs/experiments/templates/research-batch.md stays a valid DRAFT batch shape."""
    template = (REPO_ROOT / "docs/experiments/templates/research-batch.md").read_text("utf-8")
    alpha, beta = (hashlib.sha256(name.encode()).hexdigest() for name in ("a", "b"))
    filled = (
        template.replace("<b1>", "b1")
        .replace("<experiment>", "lab")
        .replace("<strategy-id-1>", "swing-alpha")
        .replace("<strategy-id-2>", "swing-beta")
        .replace("<family-1>", "breakout")
        .replace("<family-2>", "pullback")
        .replace("<sha256 of the card file>", alpha, 1)
        .replace("<sha256 of the card file>", beta, 1)
        .replace("<src/hermes/... signal module>", "src/hermes/x.py")
        .replace("<tests/... its tests>", "tests/x.py")
        .replace("<parameter_name>", "lookback")
    )

    batch = parse_batch(BATCH_PATH, filled)

    assert (batch.status, [c.section for c in batch.candidates]) == ("DRAFT", ["C1", "C2"])


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(st.integers(min_value=0, max_value=9))
def test_candidate_count_is_accepted_exactly_from_two_to_six(count: int) -> None:
    """Whatever the count, parse accepts it if and only if it is 2 to 6."""
    fm = batch_front_matter()
    base = fm["candidates"][0]
    fm["candidates"] = [
        {**base, "strategy_id": f"swing-c{i}", "idea_family": f"family-{i}", "section": "C1"}
        for i in range(count)
    ]
    body = BODY if count else ""
    try:
        parse_batch(BATCH_PATH, batch_text(fm, body))
    except BatchError as exc:
        refused: str | None = exc.check
    else:
        refused = None

    if 2 <= count <= 6:
        assert refused in {None, "section_present"}
    else:
        assert refused == "candidate_count"


@given(
    st.sampled_from(FLOORS),
    st.floats(min_value=0.0001, max_value=0.9999, allow_nan=False),
)
def test_a_floor_value_is_accepted_iff_it_is_at_least_as_strict(
    floor: Any, fraction: float
) -> None:
    """Any value on the loose side of a floor is refused; any value on the strict side is not."""
    value: float | int
    if floor.key == "trade_count_min":
        value = int(fraction * 200) + 1
    elif floor.key == "t_stat_above":
        value = fraction * 6
    else:
        value = fraction
    fm = batch_front_matter()
    fm["backtest_funnel"][floor.key] = value
    try:
        parse_batch(BATCH_PATH, batch_text(fm))
    except BatchError as exc:
        refused = exc.check == "funnel_floors"
    else:
        refused = False

    assert refused is not floor.holds(float(value))


# ---------------------------------------------------------------------------
# ADR-029 §4: the round-trip cap row (issue #1240)
# ---------------------------------------------------------------------------


def test_a_new_batch_candidate_without_the_cap_is_refused() -> None:
    """ADR-029 §4 caps every candidate's Train run, so every new candidate signs the row."""
    fm = batch_front_matter()
    del fm["candidates"][1]["round_trips_90d_max"]

    error = _refusal(fm)

    assert (error.check, error.message) == (
        "funnel_floors",
        "candidates[1] signs no round_trips_90d_max (ADR-029 §4)",
    )


def test_a_candidate_holding_under_a_day_without_the_cap_row_is_refused() -> None:
    """ADR-029 §5: a candidate that holds under a day is told why its cap is missing."""
    fm = batch_front_matter()
    del fm["candidates"][1]["round_trips_90d_max"]
    fm["candidates"][1]["holding"] = "under_a_day"

    error = _refusal(fm)

    assert (error.check, error.message) == (
        "funnel_floors",
        "candidates[1] holds under a day but signs no round_trips_90d_max (ADR-029 §4)",
    )


def test_each_candidate_carries_its_own_signed_cap() -> None:
    """The signed cap reaches the gate as the candidate's own number."""
    fm = batch_front_matter()
    fm["candidates"][1].update(holding="under_a_day", round_trips_90d_max=120)

    batch = parse_batch(BATCH_PATH, batch_text(fm))

    assert [c.round_trips_90d_max for c in batch.candidates] == [250, 120]


def test_a_cap_above_250_is_refused_as_looser_than_adr_029() -> None:
    """ADR-029 §4: a batch may tighten the cap, never loosen it; 251 is one past it."""
    fm = batch_front_matter()
    fm["candidates"][0]["round_trips_90d_max"] = 251

    error = _refusal(fm)

    assert (error.check, error.message) == (
        "funnel_floors",
        "candidates[0].round_trips_90d_max 251 is looser than ADR-029 §4 (<= 250)",
    )


def test_batch_check_fails_funnel_floors_for_a_missing_cap_row() -> None:
    """``hermes batch check`` names the missing row on the funnel_floors line."""
    fm = batch_front_matter()
    del fm["candidates"][0]["round_trips_90d_max"]
    text, git = signed(fm)

    assert _checks(text, git)["funnel_floors"] == BatchCheck(
        "funnel_floors", False, "candidates[0] signs no round_trips_90d_max (ADR-029 §4)"
    )


def test_batch_check_names_the_signed_caps_on_funnel_floors() -> None:
    """A batch that signs caps says so on the funnel_floors line, next to the floors."""
    fm = batch_front_matter()
    fm["candidates"][0]["round_trips_90d_max"] = 120
    text, git = signed(fm)

    assert _checks(text, git)["funnel_floors"].detail == (
        "dsr_min 0.95; every ADR-021 §5 floor held; round trips per 90 days at most [120, 250]"
    )


def test_only_the_batches_signed_before_the_cap_row_are_exempt() -> None:
    """swing-b1 and swing-b2 were signed before ADR-029 §4's row; no other batch may skip it."""
    assert frozenset({"swing-b1", "swing-b2"}) == SIGNED_BEFORE_CAP_ROW


def test_an_exempt_batch_id_parses_without_the_cap() -> None:
    """The exemption reads the batch_id the parser already uses, so an exempt batch needs no cap."""
    fm = batch_front_matter()
    fm["batch_id"] = "swing-b1"
    for candidate in fm["candidates"]:
        del candidate["round_trips_90d_max"]
    path = BATCH_PATH.replace("/b1.md", "/swing-b1.md")

    batch = parse_batch(path, batch_text(fm))

    assert [c.round_trips_90d_max for c in batch.candidates] == [None, None]


def test_the_batches_signed_before_the_cap_row_sign_no_cap() -> None:
    """swing-b1 and swing-b2 were signed without the row, so the gate adds none to them.

    Pre-registration binds: a row they did not sign must not judge them.
    """
    paths = ("swing-b1", "swing-b2")
    batches = [
        parse_batch(path, (REPO_ROOT / path).read_text("utf-8"))
        for path in (f"docs/experiments/swing-mvp1/batches/{name}.md" for name in paths)
    ]

    assert [[c.round_trips_90d_max for c in b.candidates] for b in batches] == [[None] * 4] * 2
