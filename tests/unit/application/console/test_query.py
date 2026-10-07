"""Tests for the console query parser, matcher, sort and completion (#729).

Invariants protected here:

- formatting a query and parsing it back gives the same query, and formatting
  is canonical (formatting a parsed string twice changes nothing);
- matching a query is equivalent to a plain reference filter on random rows;
- an unknown field, a missing value, a bad ``since`` or an open quote is
  rejected with a message that names the problem and the fix;
- facet counts are exact, most common first;
- sorting is stable across several keys and keeps empty values last;
- Tab completion completes field names only, uniquely or to the common prefix;
- ``level`` compares and sorts by severity (debug < info < warn < error <
  critical), ``warn`` and ``warning`` are one level, and an unknown level is
  rejected (#716).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.query import (
    LEVEL_ORDER,
    SHARED_FACETS,
    Condition,
    FacetCount,
    FreeText,
    Op,
    Query,
    QueryError,
    Since,
    SortKey,
    complete,
    completions,
    display_value,
    facet_counts,
    filter_rows,
    format_query,
    format_sort,
    level_rank,
    matches,
    parse_query,
    sort_rows,
    toggle_sort,
)

type Row = Mapping[str, object]

_NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
_FIELDS = ("coin", "kind", "year", "day", "run")

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_PLAIN = st.text(alphabet="abcXYZ019-._/", min_size=1, max_size=8)
_SPECIAL = st.text(alphabet="abc19 :<>=-", min_size=1, max_size=8)
_VALUE = st.one_of(_PLAIN, _SPECIAL)
_CONDITION = st.builds(
    Condition, field=st.sampled_from(_FIELDS), op=st.sampled_from(list(Op)), value=_VALUE
)
_FREE = st.builds(FreeText, text=_VALUE)
_SINCE = st.builds(Since, amount=st.integers(1, 999), unit=st.sampled_from(["h", "d"]))
_QUERY = st.builds(
    Query, terms=st.lists(st.one_of(_CONDITION, _FREE, _SINCE), max_size=6).map(tuple)
)

_COINS = ("BTC", "ETH", "SOL")
_KINDS = ("opened", "closed", "warning")
_ROW = st.fixed_dictionaries(
    {
        "coin": st.sampled_from(_COINS),
        "kind": st.sampled_from(_KINDS),
        "year": st.integers(2024, 2027),
        "at": st.integers(0, 24 * 20).map(lambda h: _NOW - timedelta(hours=h)),
    }
)
_ROW_CONDITION = st.one_of(
    st.builds(
        Condition,
        field=st.just("coin"),
        op=st.just(Op.EQ),
        value=st.sampled_from([*_COINS, "btc", "DOGE"]),
    ),
    st.builds(Condition, field=st.just("kind"), op=st.just(Op.EQ), value=st.sampled_from(_KINDS)),
    st.builds(
        Condition,
        field=st.just("year"),
        op=st.sampled_from(list(Op)),
        value=st.integers(2023, 2028).map(str),
    ),
)
_ROW_QUERY = st.builds(
    Query,
    terms=st.lists(
        st.one_of(
            _ROW_CONDITION,
            st.builds(FreeText, text=st.sampled_from(["bt", "TH", "open", "zzz", "202"])),
            st.builds(Since, amount=st.integers(1, 30), unit=st.sampled_from(["h", "d"])),
        ),
        max_size=4,
    ).map(tuple),
)


def _reference(rows: Sequence[Row], query: Query) -> list[Row]:
    """Filter the slow, obvious way, for the matcher to agree with."""
    kept = []
    for row in rows:
        ok = True
        for term in query.terms:
            if isinstance(term, Since):
                hours = term.amount * (24 if term.unit == "d" else 1)
                at = row["at"]
                assert isinstance(at, datetime)
                ok = ok and at >= _NOW - timedelta(hours=hours)
            elif isinstance(term, FreeText):
                haystack = " ".join(str(v) for v in row.values()).lower()
                ok = ok and term.text.lower() in haystack
            elif term.field == "year":
                year, wanted = int(str(row["year"])), int(term.value)
                ok = (
                    ok
                    and {
                        Op.EQ: year == wanted,
                        Op.GE: year >= wanted,
                        Op.LE: year <= wanted,
                        Op.GT: year > wanted,
                        Op.LT: year < wanted,
                    }[term.op]
                )
            else:
                ok = ok and str(row[term.field]).lower() == term.value.lower()
        if ok:
            kept.append(row)
    return kept


# ---------------------------------------------------------------------------
# Round trip and equivalence (property tests)
# ---------------------------------------------------------------------------


@given(_QUERY)
def test_format_then_parse_gives_the_same_query(query: Query) -> None:
    """Any query object survives being written out and read back."""
    assert parse_query(format_query(query), _FIELDS) == query


@given(_QUERY)
def test_formatting_is_canonical(query: Query) -> None:
    """Formatting a parsed string a second time changes nothing."""
    once = format_query(parse_query(format_query(query), _FIELDS))
    assert format_query(parse_query(once, _FIELDS)) == once


@given(st.lists(_ROW, max_size=12), _ROW_QUERY)
def test_matching_equals_the_reference_filter(rows: list[Row], query: Query) -> None:
    """filter_rows keeps exactly the rows the reference filter keeps, in order."""
    assert list(filter_rows(rows, query, now=_NOW)) == _reference(rows, query)


@given(st.lists(_ROW, max_size=12))
def test_empty_query_keeps_every_row(rows: list[Row]) -> None:
    """An empty query is no filter at all."""
    assert list(filter_rows(rows, parse_query("", _FIELDS), now=_NOW)) == rows


# ---------------------------------------------------------------------------
# Parsing examples
# ---------------------------------------------------------------------------


def test_parse_reads_every_kind_of_term_in_order() -> None:
    """Conditions, comparisons, since and free text keep their written order."""
    query = parse_query('coin:BTC year>=2025 since:7d "stop raised" day<2026-10-01 late', _FIELDS)
    assert query.terms == (
        Condition("coin", Op.EQ, "BTC"),
        Condition("year", Op.GE, "2025"),
        Since(7, "d"),
        FreeText("stop raised"),
        Condition("day", Op.LT, "2026-10-01"),
        FreeText("late"),
    )


def test_field_names_and_since_units_are_case_insensitive() -> None:
    """``Coin:`` reads as ``coin:`` and ``since:24H`` as 24 hours."""
    query = parse_query("Coin:eth SINCE:24H", _FIELDS)
    assert (query.terms, query.since) == (
        (Condition("coin", Op.EQ, "eth"), Since(24, "h")),
        (timedelta(hours=24),),
    )


def test_query_splits_its_terms_by_kind() -> None:
    """conditions, text and since give each kind of term, in order."""
    query = parse_query("a coin:BTC since:2d b", _FIELDS)
    assert (query.conditions, query.text, query.since) == (
        (Condition("coin", Op.EQ, "BTC"),),
        ("a", "b"),
        (timedelta(days=2),),
    )


def test_quoted_value_keeps_its_spaces() -> None:
    """``coin:"a b"`` is one condition whose value holds the space."""
    assert parse_query('coin:"a b"', _FIELDS).terms == (Condition("coin", Op.EQ, "a b"),)


def test_text_with_a_colon_but_no_field_name_is_free_text() -> None:
    """``12:30`` has no letters before the colon, so it is searched as text."""
    assert parse_query("12:30", _FIELDS).terms == (FreeText("12:30"),)


def test_empty_quotes_are_ignored() -> None:
    """``""`` adds no term."""
    assert parse_query('"" coin:BTC', _FIELDS).terms == (Condition("coin", Op.EQ, "BTC"),)


def test_shared_facets_are_the_default_fields() -> None:
    """Without a field list, the shared facets are accepted."""
    assert SHARED_FACETS == (
        "year",
        "month",
        "day",
        "coin",
        "kind",
        "state",
        "level",
        "service",
        "trace",
        "run",
    )
    assert parse_query("level:error state:open month:2026-10").terms == (
        Condition("level", Op.EQ, "error"),
        Condition("state", Op.EQ, "open"),
        Condition("month", Op.EQ, "2026-10"),
    )


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------


def test_unknown_field_names_the_known_ones_and_a_close_match() -> None:
    """A typo names the field, suggests the nearest one and lists the rest."""
    with pytest.raises(QueryError) as caught:
        parse_query("coins:BTC", _FIELDS)
    assert str(caught.value) == (
        'unknown field "coins" (did you mean "coin"?); fields: coin, day, kind, run, since, year'
    )


def test_unknown_field_without_a_close_match_lists_the_fields() -> None:
    """A field nothing like a known one gets the list only."""
    with pytest.raises(QueryError) as caught:
        parse_query("price>5", ("coin", "kind"))
    assert str(caught.value) == 'unknown field "price"; fields: coin, kind, since'


def test_missing_value_says_what_to_write() -> None:
    """``coin:`` with nothing after it asks for a value with an example."""
    with pytest.raises(QueryError) as caught:
        parse_query("coin:", _FIELDS)
    assert str(caught.value) == '"coin:" needs a value, like coin:BTC'


def test_empty_quoted_value_is_a_missing_value() -> None:
    """``coin:""`` is as empty as ``coin:`` and gets the same message."""
    with pytest.raises(QueryError) as caught:
        parse_query('coin:""', _FIELDS)
    assert str(caught.value) == '"coin:" needs a value, like coin:BTC'


@pytest.mark.parametrize("text", ["since:7", "since:0d", "since:7w", "since>=7d", "since:d"])
def test_bad_since_shows_the_accepted_form(text: str) -> None:
    """The since term takes a positive whole number of hours or days, with a colon."""
    with pytest.raises(QueryError) as caught:
        parse_query(text, _FIELDS)
    assert str(caught.value) == "since takes hours or days, like since:24h or since:7d"


def test_open_quote_asks_for_the_closing_quote() -> None:
    """A quote left open is reported, not guessed at."""
    with pytest.raises(QueryError) as caught:
        parse_query('coin:BTC "stop', _FIELDS)
    assert str(caught.value) == 'a quote is not closed; add the closing "'


def test_quote_inside_a_word_is_rejected() -> None:
    """Quotes wrap a whole value or a whole text, never part of one."""
    with pytest.raises(QueryError) as caught:
        parse_query('ab"c d"', _FIELDS)
    assert str(caught.value) == 'quotes must wrap a whole value, like "two words"'


def test_query_error_is_a_value_error() -> None:
    """Callers that catch ValueError also catch a bad query."""
    assert issubclass(QueryError, ValueError)


# ---------------------------------------------------------------------------
# Matching details
# ---------------------------------------------------------------------------


def test_numbers_compare_as_numbers_not_text() -> None:
    """``count>9`` keeps 10, which a text comparison would drop."""
    rows: list[Row] = [{"count": 10}, {"count": 9}, {"count": Decimal("9.5")}]
    query = parse_query("count>9", ("count",))
    assert list(filter_rows(rows, query, now=_NOW)) == [{"count": 10}, {"count": Decimal("9.5")}]


def test_a_number_field_never_matches_a_word() -> None:
    """``year:abc`` matches nothing instead of failing."""
    assert matches(parse_query("year:abc", _FIELDS), {"year": 2026}, now=_NOW) is False


def test_missing_or_empty_field_never_matches_a_condition() -> None:
    """A row without the field, or with None in it, fails the condition."""
    query = parse_query("coin:BTC", _FIELDS)
    assert (matches(query, {}, now=_NOW), matches(query, {"coin": None}, now=_NOW)) == (
        False,
        False,
    )


def test_dates_compare_by_their_iso_text() -> None:
    """``day>=2026-10-01`` keeps the 1st and later, given date values."""
    rows: list[Row] = [{"day": date(2026, 9, 30)}, {"day": date(2026, 10, 1)}]
    query = parse_query("day>=2026-10-01", _FIELDS)
    assert list(filter_rows(rows, query, now=_NOW)) == [{"day": date(2026, 10, 1)}]


def test_since_needs_a_timestamp_on_the_row() -> None:
    """A row without an ``at`` datetime never passes ``since``."""
    query = parse_query("since:1d", _FIELDS)
    assert matches(query, {"at": "yesterday"}, now=_NOW) is False


def test_since_includes_the_boundary() -> None:
    """A row exactly 24 hours old passes ``since:24h``."""
    query = parse_query("since:24h", _FIELDS)
    assert matches(query, {"at": _NOW - timedelta(hours=24)}, now=_NOW) is True


def test_display_value_of_each_type() -> None:
    """Values print the way a table cell and a facet show them."""
    assert [
        display_value(None),
        display_value(Decimal("1.50")),
        display_value(7),
        display_value(date(2026, 10, 5)),
        display_value("BTC"),
    ] == ["", "1.50", "7", "2026-10-05", "BTC"]


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


def test_facet_counts_are_exact_and_most_common_first() -> None:
    """Counts per value, ties broken by value; empty values are not a facet."""
    rows: list[Row] = [
        {"coin": "ETH"},
        {"coin": "BTC"},
        {"coin": "SOL"},
        {"coin": "BTC"},
        {"coin": None},
        {},
        {"coin": "ETH"},
        {"coin": "BTC"},
    ]
    assert facet_counts(rows, "coin") == (
        FacetCount("BTC", 3),
        FacetCount("ETH", 2),
        FacetCount("SOL", 1),
    )


@given(st.lists(_ROW, max_size=20))
def test_facet_counts_add_up_to_the_rows(rows: list[Row]) -> None:
    """Every row with a value is counted exactly once."""
    assert sum(c.count for c in facet_counts(rows, "kind")) == len(rows)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_sort_is_stable_across_keys() -> None:
    """Ties on every key keep their incoming order."""
    rows: list[Row] = [
        {"id": 1, "coin": "ETH", "year": 2026},
        {"id": 2, "coin": "BTC", "year": 2025},
        {"id": 3, "coin": "ETH", "year": 2025},
        {"id": 4, "coin": "BTC", "year": 2025},
        {"id": 5, "coin": "ETH", "year": 2026},
    ]
    ordered = sort_rows(rows, (SortKey("coin"), SortKey("year", descending=True)))
    assert [r["id"] for r in ordered] == [2, 4, 1, 5, 3]


def test_empty_values_sort_last_both_ways() -> None:
    """None stays at the bottom whether the sort goes up or down."""
    rows: list[Row] = [{"n": None}, {"n": 2}, {"n": 1}]
    assert (
        [r["n"] for r in sort_rows(rows, (SortKey("n"),))],
        [r["n"] for r in sort_rows(rows, (SortKey("n", descending=True),))],
    ) == ([1, 2, None], [2, 1, None])


def test_text_sorts_without_case_and_numbers_by_value() -> None:
    """``b`` sorts after ``A``; 10 sorts after 9."""
    rows: list[Row] = [{"v": "b"}, {"v": "A"}, {"v": 10}, {"v": 9}]
    assert [r["v"] for r in sort_rows(rows, (SortKey("v"),))] == [9, 10, "A", "b"]


@given(st.lists(_ROW, max_size=15))
def test_sort_by_one_key_matches_pythons_stable_sort(rows: list[Row]) -> None:
    """A single key orders like sorted() with the same key, ties untouched."""
    expected = sorted(rows, key=lambda r: str(r["coin"]).lower())
    assert list(sort_rows(rows, (SortKey("coin"),))) == expected


def test_toggle_sort_flips_the_primary_and_promotes_a_new_one() -> None:
    """Clicking the sorted column flips it; another column becomes primary."""
    keys = toggle_sort((), "coin")
    flipped = toggle_sort(keys, "coin")
    promoted = toggle_sort(flipped, "year")
    assert (keys, flipped, promoted) == (
        (SortKey("coin"),),
        (SortKey("coin", descending=True),),
        (SortKey("year"), SortKey("coin", descending=True)),
    )


def test_toggle_sort_keeps_at_most_three_keys() -> None:
    """Old keys fall off past three so the indicator stays short."""
    keys: tuple[SortKey, ...] = ()
    for field in ("a", "b", "c", "d"):
        keys = toggle_sort(keys, field)
    assert keys == (SortKey("d"), SortKey("c"), SortKey("b"))


def test_format_sort_draws_arrows() -> None:
    """The indicator lists keys in priority order with up/down arrows."""
    assert (format_sort(()), format_sort((SortKey("coin"), SortKey("at", True)))) == (
        "",
        "coin ▲ · at ▼",
    )


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_completions_list_matching_fields_and_since() -> None:
    """Field names starting with the prefix, since included, sorted."""
    assert (completions("", _FIELDS), completions("s", _FIELDS), completions("K", _FIELDS)) == (
        ("coin", "day", "kind", "run", "since", "year"),
        ("since",),
        ("kind",),
    )


@pytest.mark.parametrize(
    ("text", "fields", "expected"),
    [
        ("co", _FIELDS, "coin:"),
        ("year>=2025 ki", _FIELDS, "year>=2025 kind:"),
        ("m", ("month", "money"), "mon"),
        ("", _FIELDS, ""),
        ("year>=2025 ", _FIELDS, "year>=2025 "),
        ("coin:B", _FIELDS, "coin:B"),
        ("zz", _FIELDS, "zz"),
        ('"co', _FIELDS, '"co'),
    ],
)
def test_complete_fills_a_unique_field_or_the_common_prefix(
    text: str, fields: Sequence[str], expected: str
) -> None:
    """Tab adds ``field:`` when one field fits, the shared prefix when several do."""
    assert complete(text, fields) == expected


# ---------------------------------------------------------------------------
# Level order (#716)
# ---------------------------------------------------------------------------

_LEVEL_ROWS: tuple[Row, ...] = tuple(
    {"level": name, "event": f"e{index}"} for index, name in enumerate(LEVEL_ORDER)
)
_LEVEL_FIELDS = ("level", "service", "coin", "trace")
_OPS = {
    Op.EQ: lambda a, b: a == b,
    Op.GE: lambda a, b: a >= b,
    Op.LE: lambda a, b: a <= b,
    Op.GT: lambda a, b: a > b,
    Op.LT: lambda a, b: a < b,
}


def test_level_ge_warn_keeps_warnings_and_worse() -> None:
    """``level>=warn`` keeps warning, error and critical, not debug or info."""
    query = parse_query("level>=warn", _LEVEL_FIELDS)
    kept = filter_rows(_LEVEL_ROWS, query, now=_NOW)
    assert [row["level"] for row in kept] == ["warning", "error", "critical"]


def test_warn_and_warning_are_the_same_level() -> None:
    """``level:warn`` matches a row logged as ``warning``, in any case."""
    query = parse_query("level:WARN", _LEVEL_FIELDS)
    assert [row["level"] for row in filter_rows(_LEVEL_ROWS, query, now=_NOW)] == ["warning"]


def test_level_lt_compares_by_severity_not_spelling() -> None:
    """``level<info`` keeps debug only, although "error" < "info" alphabetically."""
    query = parse_query("level<info", _LEVEL_FIELDS)
    assert [row["level"] for row in filter_rows(_LEVEL_ROWS, query, now=_NOW)] == ["debug"]


def test_an_unknown_level_is_rejected_with_the_levels() -> None:
    """``level>=loud`` names the levels the field takes."""
    with pytest.raises(QueryError) as caught:
        parse_query("level>=loud", _LEVEL_FIELDS)
    assert str(caught.value) == (
        "level takes debug, info, warn, error or critical, like level>=warn"
    )


def test_level_rank_orders_the_levels() -> None:
    """Ranks run 0..4 from debug to critical; warn aliases warning; other text is None."""
    assert (
        [level_rank(name) for name in ("debug", "Info", "warn", "warning", "ERROR", "critical")],
        level_rank("loud"),
        level_rank(3),
    ) == ([0, 1, 2, 2, 3, 4], None, None)


def test_sorting_by_level_follows_severity() -> None:
    """A level column sorts debug, info, warning, error, critical; descending reverses it."""
    shuffled = [_LEVEL_ROWS[i] for i in (3, 0, 4, 2, 1)]
    up = [row["level"] for row in sort_rows(shuffled, [SortKey("level")])]
    down = [row["level"] for row in sort_rows(shuffled, [SortKey("level", descending=True)])]
    assert (up, down) == (list(LEVEL_ORDER), list(reversed(LEVEL_ORDER)))


def test_service_and_trace_are_shared_fields() -> None:
    """``service:engine trace:K7Q2ZP`` parses with the default field set."""
    assert parse_query("service:engine trace:K7Q2ZP").terms == (
        Condition("service", Op.EQ, "engine"),
        Condition("trace", Op.EQ, "K7Q2ZP"),
    )


def test_tab_completes_service() -> None:
    """``se`` completes to ``service:`` among the log fields (``since`` starts with "si")."""
    assert complete("level>=warn se", _LEVEL_FIELDS) == "level>=warn service:"


@given(
    st.lists(st.sampled_from(LEVEL_ORDER), max_size=12),
    st.sampled_from(list(Op)),
    st.sampled_from([*LEVEL_ORDER, "warn", "WARN", "Error"]),
)
def test_level_conditions_match_the_severity_order(levels: list[str], op: Op, written: str) -> None:
    """Any level condition keeps exactly the rows whose rank compares as the operator says."""
    rows = [{"level": name} for name in levels]
    query = Query((Condition("level", op, written),))
    wanted = LEVEL_ORDER.index({"warn": "warning"}.get(written.lower(), written.lower()))
    expected = [r for r in rows if _OPS[op](LEVEL_ORDER.index(str(r["level"])), wanted)]
    assert list(filter_rows(rows, query, now=_NOW)) == expected
