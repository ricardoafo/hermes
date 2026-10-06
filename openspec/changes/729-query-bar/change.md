# Change: 729-query-bar

**SDD change**: `729-query-bar`
**Date**: 2026-10-05
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/729-query-bar/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Every console list (logs, tax lots, briefs, signals, activity) needs the same
way to query, filter and sort. This change adds one pure query language in the
application layer and three reusable Textual widgets on top of it, shown first
on a demo activity list. Later list screens (issues #716, #719, #720, #714)
embed the same `QueryList` instead of writing their own filter.

## Scope

### In

- `application/console/query.py`: parser, formatter, matcher, stable
  multi-key sort, facet counts, Tab completion of field names.
- `application/console/activity.py`: `activity_query_rows` and
  `ACTIVITY_QUERY_FIELDS`, the activity feed as query rows.
- `entrypoints/tui_query.py`: `QueryBar`, `FacetFilter`, `SortableTable`,
  `QueryList`, and the demo `QueryHarnessApp` / `activity_query_app`.

### Out

- Wiring into `hermes tui` or any existing screen: the home screen's activity
  card is a four-row Rich card, not a list, so the first use is a harness.
- Own colours: the widgets read only the `$hud-*` variables of the console
  theme (issue #711) and the harness sits in a `HudPanel`, so Neon and Control
  room restyle them with no colour of their own.
- Completing values (only field names complete).

## Decisions

### D-1: one line of text, terms in written order, all required

`field:value`, `field>=value` (and `<=`, `>`, `<`), `since:7d` / `since:24h`,
and free text; quotes keep spaces. A `Query` keeps its terms in order so
`parse_query(format_query(q)) == q` holds and the line the operator typed can
be written back canonically. There is no OR: lists are short and facets show
the alternatives.

### D-2: strict parsing with messages the bar shows as they are

An unknown field, a missing value, a bad `since` or a misplaced quote raises
`QueryError` (a `ValueError`). The message names the problem and the fix
(`unknown field "coins" (did you mean "coin"?); fields: ...`). The bar keeps
the last good result while the line does not parse.

### D-3: rows are mappings; comparison follows the row's value type

A list exposes rows as `Mapping[str, object]`. Numbers compare as numbers,
dates as ISO text, everything else as case-insensitive text. A missing or
empty field fails its condition. `since:` reads the row's `at` datetime and a
`now` the caller passes, so tests and the TUI agree.

### D-4: the shared facets are names, each list picks its own

`SHARED_FACETS` is year, month, day, coin, kind, state, level, run. A list
passes the subset its rows carry (activity: year, month, day, coin, kind), so
`run:` on the activity list is an unknown field, not a silent empty result.

### D-5: stable multi-key sort, empty values last, at most three keys

A header click or `S` on a column makes it the primary key (or flips it when
it already leads); earlier keys stay behind it, three at most. Ties keep their
incoming order, and empty values sort last in both directions.

### D-6: activity rows carry no money

`activity_query_rows` leaves out price and net, so the same rows suit any
surface, supervisor ones included.

## Requirements

### R-1: formatting a parsed query and parsing it back gives the same query

### R-2: matching equals a plain reference filter on random rows

### R-3: unknown fields, missing values, bad `since` and open quotes give a clear message

### R-4: facet counts are exact, most common first, ties by value

### R-5: sorting is stable over several keys and keeps empty values last

### R-6: Tab completes a unique field to `field:`, several to their common prefix

### R-7: the bar shows "N of M" and the sort indicator, or the parse error

### R-8: a header click or `S` sorts by that column; again flips the direction

## Design

`query.py` tokenizes on unquoted whitespace, reads each token as a quoted
text, a `field op value` condition (`since` handled apart) or free text, and
validates the field against the list's names. Matching is `all(term holds)`.
`sort_rows` applies the keys lowest priority first with Python's stable sort,
moving empty values to the end on each pass.

`QueryList` composes `QueryBar` → `FacetFilter` → `SortableTable`. The bar
posts `QueryBar.Changed` when the line parses; the table posts
`SortableTable.SortChanged` on a header click or `S`. `QueryList` then filters
with `filter_rows`, sorts with `sort_rows`, and redraws the table, the facet
counts and the status line. Enter on the query line moves the focus to the
table; Tab in the line completes instead of moving the focus.

## Test plan

- `tests/unit/application/console/test_query.py`: Hypothesis round trip and
  canonical form, matcher vs reference filter, facet counts summing to the
  rows, single-key sort vs `sorted`; unit tests for every error message,
  number/date/text comparison, stable multi-key sort, toggling, completion.
- `tests/unit/application/console/test_activity.py`: activity query rows.
- `tests/unit/entrypoints/test_tui_query.py`: Textual `run_test` over the
  harness (typing, errors, Tab, Enter, `S`, header click) and SVG snapshots
  at 80 and 160 columns.

## Risks

- The query language is now a contract many screens share; new syntax must
  keep R-1 (round trip) or older saved lines read differently.
- Free text searches every value of a row, including hidden ones such as `at`;
  a list that adds a sensitive field to its rows makes it searchable.

## Dependencies on other changes

- Issue #711 (console theme and tokens) restyles the widgets.
- Issues #714, #716, #719, #720 embed `QueryList`.
