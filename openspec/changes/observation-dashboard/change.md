# Change: observation-dashboard

**SDD change**: `observation-dashboard`
**Date**: 2026-10-04
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#33)

Mirror of engram topic key `sdd/observation-dashboard/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes observe` is a console panel for the 90-day shadow observation window
(pre-registration v1 §7): day N of 90, fills and rejections, fill drift p50/p95
against the 30 bps SHADOW_CERTIFIED gate with a pass / at risk / fail label,
slippage distribution, decision latency, data gaps and late bars, uptime and
restarts, and the state of the three §10 shadow-phase kill triggers. The
operator console may add shadow P&L under the 2026-10-03 decision record
(`pnl_visible`); every other surface is blind. A pure `weekly_report` function
produces the blind text for a pushed Slack or Telegram message. The view
carries raw per-fill samples, so it runs on fixture scenarios today and a
ledger read model fills it from `FillQualityRecorded` (#353) later. Main risk:
a P&L figure leaking into the blind output, guarded by tests that render with
and without money and require identical output.

## Scope

### In

- View objects `ObservationWindow`, `ObservationStats`, `KillTrigger`,
  `ObservationMoney` in `application/console/views.py`, and
  `ConsoleReadModel.observation()`.
- `application/console/observation.py`: percentiles, histogram, the drift
  verdict and the gate constants (pure).
- `application/console/observation_render.py`: the Rich panel.
- `application/console/weekly_report.py`: the blind weekly text.
- `observation_to_json` beside the renderer in `observation_render.py`
  (`serialize.py` holds the positions JSON only).
- An `observation` object in all six fixture scenarios.
- `hermes observe [--json] [--utc] [--weekly]` as a console panel module
  (`wants_demo=True`).

### Out

- The ledger-backed observation read model (needs #353 merged: it reads
  `FillQualityRecorded` rows with `book_source = live`). Follow-up.
- Sending the weekly report (systemd timer, Sunday 20:00 Europe/Madrid, Slack
  and Telegram push). `hermes observe --weekly` prints the text the timer will
  send. Follow-up.
- The monthly and yearly reports (ADR-008 §5) and the `hermes report` command.
- A TUI screen for the panel.
- Any edit to the pre-registration.

## Decisions

### D-1: Command name `observe`

- **Decision**: The panel is `hermes observe`.
- **Alternatives**: `status` (taken: the bar-loop progress check); `health`
  (taken: heartbeats and services); `report` (ADR-003 §07 reserves it for the
  weekly, monthly and yearly report generator).
- **Rationale**: The ADR-003 §07 catalog has no command for the observation
  window, and `observe` names what the window is for.
- **Cost of reversal**: LOW (one module name).

### D-2: The view carries raw samples; the application computes percentiles

- **Decision**: `ObservationStats` holds the signed drift (bps), slippage (bps)
  and decision latency (ms) of each fill or decision. `observation.py` computes
  p50, p95 and max with linear interpolation in exact `Decimal`, and the drift
  gate reads p50 of the absolute drift, as #353 D-4 specifies
  (`p50(|drift_bps|)` over live-book fills with a model price).
- **Alternatives**: precomputed p50/p95 in the view (the fixture could then
  contradict its own samples, and the ledger adapter would duplicate the
  maths); a new domain module (the figures are display measures, not a gate
  decision; the gate stays in `certification_gates`).
- **Rationale**: One place computes the figures, for fixtures and the ledger
  alike, and it can be tested with hypothesis.
- **Cost of reversal**: LOW.

### D-3: Thresholds are console constants checked against the pre-registration

- **Decision**: `DRIFT_GATE_BPS = 30`, the kill limits (7 days, 3 shocks in 30
  days, Sharpe < 0.3 for 60 days) and `WINDOW_DAYS = 90` are constants in
  `observation.py`. A unit test compares them with
  `PreRegistration().certification_schedule()` and `.kill_triggers()`, and
  the limits that have no accessor (7 rolling days, 3 fires in 30 days) with
  the §10 text of the signed file.
- **Alternatives**: reading `PreRegistration` at render time (it reads the
  docs file, which an installed wheel does not ship); thresholds in the
  fixture (a scenario could move the gate).
- **Rationale**: The panel works from a wheel, and a pre-registration edit
  breaks the test instead of silently diverging.
- **Cost of reversal**: LOW.

### D-4: Drift verdict labels

- **Decision**: `no live fills yet` with no samples; `fail` when p50 of
  |drift| is 30 bps or more (the gate is strictly below 30); `at risk` from
  24 bps (80% of the gate); `pass` below that.
- **Alternatives**: no at-risk band (the operator learns of a breach only
  when it happens); an at-risk rule on p95 (p95 is not the gated statistic).
- **Rationale**: The label is a display aid, never a state transition; the
  gate itself stays in `certification_gates` and runs once.
- **Cost of reversal**: LOW.

### D-5: Blindness by construction

- **Decision**: Money (shadow net P&L, Sharpe) and the Sharpe kill trigger's
  day count appear only when `pnl_visible(ctx.surface)`. Blind surfaces show
  the Sharpe trigger as armed or fired, with no count. `weekly_report` takes
  no surface and never reads `ObservationWindow.money` or the Sharpe count.
- **Alternatives**: a `surface` argument on the weekly report (a caller could
  pass an operator surface into a pushed message).
- **Rationale**: Decision record 2026-10-03 rule 4 and #319: pushed messages
  and reports are durable records AI sessions read, so they stay blind.
- **Cost of reversal**: LOW.

### D-6: Fixture `observation` is required; day and length come from `shadow_test`

- **Decision**: Every scenario gets an `observation` object. The window's day
  and length are the scenario's `shadow_test`, so the two lines cannot
  disagree. Observation snapshots live in their own snapshot file, so the
  existing console snapshots stay byte-identical.
- **Alternatives**: an optional key with a default (a scenario on day 12 with
  zero fills would look valid); new scenarios (adds rows to every existing
  snapshot test).
- **Cost of reversal**: LOW.

## Requirements

### R-1: The panel shows the observation window

- **A-1.1**: `uv run hermes --demo ok-two-positions observe` exits 0 and
  prints `day 12 of 90`, the drift verdict and the kill triggers.
- **A-1.2**: `uv run hermes --demo ok-two-positions observe --json` exits 0
  and prints JSON with `drift.verdict`.
- **A-1.3**: `uv run hermes observe` (no demo) exits 0 with a no-data note.

### R-2: Blind surfaces show no money

- **A-2.1**: `uv run pytest tests/unit/application/console/test_observation_render.py -q`
  exits 0, including the test that blind output is identical with and without
  money.
- **A-2.2**: `uv run pytest tests/unit/application/console/test_weekly_report.py -q`
  exits 0, including the same test for the weekly report.

### R-3: The drift gate matches the pre-registration

- **A-3.1**: `uv run pytest tests/unit/application/console/test_observation.py -q`
  exits 0, including the constants check against `PreRegistration`.

### R-4: Existing behaviour unchanged

- **A-4.1**: `git diff origin/main -- tests/unit/application/console/__snapshots__/test_render_snapshots.ambr`
  is empty.
- **A-4.2**: `uv run pytest tests/unit tests/integration -q` exits 0.

## Design

```text
src/hermes/application/console/
  observation.py          percentile, summarize -> Distribution, histogram,
                          DriftVerdict, drift_verdict, gate constants
  views.py                + KillCriterion, KillState, KillTrigger,
                          ObservationStats, ObservationMoney, ObservationWindow
  read_model.py           + observation() -> ObservationWindow
  observation_render.py   render_observation(window, ctx) -> Group,
                          observation_to_json(window, ctx)
  weekly_report.py        weekly_report(window, *, demo_scenario, tz) -> str
  render.py               + framed(ctx, parts) (public alias of _framed)
src/hermes/adapters/console/fixture_read_model.py  + _observation parser
src/hermes/adapters/console/scenarios/*.json       + "observation"
src/hermes/entrypoints/commands/observe.py         COMMAND(wants_demo=True)
```

Fixture shape:

```json
"observation": {
  "started_on": "2026-09-22",
  "last_7_days": {"fills": 4, "rejections": 0, "drift_bps": ["5.1"],
                  "slippage_bps": ["3.0"], "decision_latency_ms": ["210"],
                  "data_gaps": 0, "late_bars": 1, "uptime_pct": "100.0",
                  "restarts": 0},
  "to_date": {"...": "same fields"},
  "kill_triggers": [{"criterion": "drift_sustained", "state": "armed", "count": 0}],
  "money": {"net": "41.20", "sharpe": "0.82"}
}
```

Kill criteria (pre-registration §10, shadow phase): `drift_sustained` (drift
p50 > 30 bps for 7 rolling days; count = days), `shock_overlay` (SA-9 rule
fires 3+ times in 30 days; count = fires), `shadow_sharpe` (Sharpe < 0.3 for
60 consecutive days; count = days, P&L-revealing).

## Test plan

1. `observation.py`: percentile examples and hypothesis properties (bounded
   by min and max, monotone in the percentile, p50 equals
   `statistics.median`); verdict boundaries at 24 and 30 bps; constants
   against `PreRegistration`.
2. Renderer: behaviour assertions (day N, verdict, kill states), blind output
   identical with and without money, no money strings on blind surfaces;
   snapshots per scenario at 80 and 120 columns (operator) and 80 (blind).
3. Weekly report: blind invariant, demo tag, not-started text, snapshots.
4. JSON: money keys only on operator surfaces.
5. Fixture: every scenario parses an observation; bad criterion or state fails
   on load.
6. Command: demo, `--json`, `--weekly`, no-data note, usage errors.

Test tiers touched: unit

## Risks

- **Risk 1**: a P&L-derived figure added later to the panel without the
  `shows_money` gate. Mitigated by the identical-output tests, which fail for
  any money field that reaches blind output.
- **Risk 2**: #353 changes the drift definition before merging. The view
  holds signed samples and the adapter does the filtering, so only the
  adapter would change.

## Dependencies on other changes

- Builds on #525 (console panel registry) and the console read model.
- The ledger read model for this panel depends on #353.
