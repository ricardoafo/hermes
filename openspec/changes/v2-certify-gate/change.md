# Change: v2-certify-gate

**SDD change**: `v2-certify-gate`
**Date**: 2026-10-17
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/v2-certify-gate/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes certify swing-mvp1-v2` evaluates v2's backtest gate as the signed
`pre-registration-v2.md` §10 states it: DSR ≥ 0.95, the §9 secondary rows v2
keeps unchanged from v1, the calibration fill, and the ADR-021 §5 funnel. The
DSR floor is read from the signed v2 file's §10 text; the secondary thresholds
are v1's, reused from the v1 accessor after checking v2 §9 still says
"Unchanged from v1"; the funnel thresholds come from the v2 front matter
through the existing funnel parser. v1's gate, output and records are
unchanged. Main risk: a v2 gate that passed without the funnel would put an
unfunnelled v2 into shadow, so a missing funnel evidence file fails the gate.

## Scope

### In

- `application/calibration/certification_gates_v2.py`: v2's rules
  (`V2BacktestRules`, `parse_v2_backtest_rules`) and the gate
  (`evaluate_v2_gate`), pure.
- `entrypoints/certify.py`: accept `swing-mvp1-v2`, the `--funnel-evidence`
  flag (v2 only), the v2 roster fingerprint, and a `gate` hook on `run`.
- Unit and property tests; `changelog.d/1008-added.md`.

### Out

- v2's shadow, OOS, live-approval and live gates. v2 §7 changes the shadow
  rule (90 days and 30 round trips, 180-day cap); certify refuses every v2
  gate past `UNTRUSTED` until that lands, consuming nothing.
- The `swing-mvp1-v2` roster entry in `config/strategies.yaml` (PR #1007).
- Workflow edits: `backtest.yml` must pass `--funnel-evidence` for v2.
- The ADR-012 D-11 "implied independent trials" N (v2 §8.1): the DSR keeps
  reading the trial stats the performance file carries.

## Decisions

### D-1: The DSR floor is read from the signed v2 file

- **Decision**: `parse_v2_backtest_rules` takes the one `- DSR < X;` bullet
  of §10 "Backtest phase" and passes at `dsr >= X`.
- **Alternatives**: a constant mirroring the text, as v1's accessor does (two
  copies of 0.95); a new front-matter key (edits a signed, immutable file).
- **Rationale**: the issue asks for one source. The file is immutable, so
  the parse runs against one text; zero or two matching bullets refuse.
- **Cost of reversal**: LOW.

### D-2: v2's secondary rows are v1's rows, reused

- **Decision**: the v2 gate evaluates v1's backtest gate with v1's
  `PreRegistration` and replaces only the `dsr` row. The parser refuses a v2
  file whose §9 "Secondary" paragraph does not start "Unchanged from v1".
- **Alternatives**: a v2 `H1Thresholds` copy (duplicates six numbers).
- **Rationale**: v2 §9 says the secondaries are unchanged from v1; §10's
  extra "max drawdown ≥ 15%" is the same row. Reusing v1's gate also keeps the
  OOS-only exclusions and the calibration fill row identical.
- **Cost of reversal**: LOW.

### D-3: The funnel is part of v2's backtest gate, fail-closed

- **Decision**: `--funnel-evidence FILE` adds the ten funnel rows (thresholds
  from the v2 front matter). Without it, one `funnel` row fails.
- **Alternatives**: leave the funnel to `hermes funnel` only, so certify
  records `BACKTEST_CERTIFIED` on DSR and secondaries alone.
- **Rationale**: v2 §7: "`BACKTEST_CERTIFIED` needs §10's backtest gate, which
  includes the funnel". The v2-backtest-funnel change deferred exactly this to
  the multi-strategy certify.
- **Cost of reversal**: LOW (drop the rows).

### D-4: v2 is fingerprinted from its own roster entry

- **Decision**: v2's fingerprint is `v1_parameter_fingerprint` of the roster
  entry named `swing-mvp1-v2` (enabled or not); no entry exits 3.
- **Alternatives**: the enabled entry, as v1 does (fingerprints v1's capital
  while v1 runs).
- **Rationale**: PR #1007 adds the v2 entry disabled; certify runs in the
  backtest workflow, not in serve, so it must not depend on which is enabled.
- **Cost of reversal**: LOW.

## Requirements

### R-1: v2's DSR boundary is exact

- **A-1.1**: DSR 0.94 fails the `dsr` row and 0.95 passes it
  (`tests/unit/application/calibration/test_certification_gates_v2.py`).

### R-2: v1 unchanged

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_certify.py
  tests/unit/application/calibration/test_certification_gates.py -W error`
  passes with neither file edited.
- **A-2.2**: `git diff origin/main -- src/hermes/application/calibration/certification_gates.py
  src/hermes/application/strategy_protocol/preregistration.py` is empty.

### R-3: Line format

- **A-3.1**: every v2 check line matches `^(PASS|FAIL)  <name>`.

## Design

```text
certify main(argv)
  strategy_id == swing-donchian-4h  -> unchanged v1 path
  strategy_id == swing-mvp1-v2
    v1 = pre_registration_for(config_dir)              # reused §9 secondaries
    v2 = v1.pre_reg_path().with_name("pre-registration-v2.md")
    rules = parse_v2_backtest_rules(text, read_front_matter(text))
    fingerprint = v1_parameter_fingerprint(<roster entry swing-mvp1-v2>)
    funnel = load_funnel_evidence(--funnel-evidence) | None; validate_evidence
    run(..., gate=lambda record, evidence, now: evaluate_v2_gate(...))
```

`evaluate_v2_gate`: DEAD and CERTIFIED as v1; UNTRUSTED evaluates v1's gate,
swaps the `dsr` row for `>= dsr_min` and appends the funnel rows; any other
state is REFUSED. The funnel evidence file's SHA-256 is folded into the
recorded evidence hash, as `--performance` does.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`,
   `uv run lint-imports` clean; certify, calibration and entrypoint unit
   suites pass with `-W error`.
3. Synthetic data only.

Test tiers touched: unit, property

## Risks

- **The §10 parse breaks on a rewrite of the file**: the file is immutable;
  a v3 needs its own rules anyway. The parser refuses rather than guesses.
- **Funnel evidence for another strategy**: `load_funnel_evidence` refuses a
  file whose `strategy_id` is not `swing-mvp1-v2`.
- **v2 recorded past BACKTEST_CERTIFIED with v1's shadow rules**: refused.

## Dependencies on other changes

- PR #1007 (roster entry). Until it merges, `hermes certify swing-mvp1-v2`
  with the shipped config exits 3 naming the missing entry.
- `openspec/changes/v2-backtest-funnel/change.md` (funnel evaluator).
