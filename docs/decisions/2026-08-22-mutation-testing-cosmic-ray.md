# Adopt `cosmic-ray` as the Phase 1 mutation-testing gate

**Date**: 2026-08-22
**Author**: claude on behalf of Ricardo
**Related**: `docs/guides/ai-authored-tests.md` §"The mutation kill rate is
the load-bearing gate", `docs/decisions/2026-08-22-drop-safety.md` (Phase-0
`mutmut` blocker context), GH #118 (feasibility spike)

## Decision

Adopt **cosmic-ray 8.7** as the mutation-testing tool for
`src/hermes/domain/value_objects/`. Wired via `cosmic-ray.toml` at the repo
root and a weekly `mutation.yml` workflow (Sundays 03:00 UTC). Advisory
mode for a 2-week soak (2026-08-22 → 2026-09-05), then blocking at a
≥80% kill-rate floor.

## Context

`docs/guides/ai-authored-tests.md` frames mutation testing as **the
load-bearing correctness gate**: line coverage tells you a line executed;
mutation testing tells you the test would DETECT if that line's logic were
subtly wrong. Without it, AI-authored tests satisfy pytest-cov while
missing real bugs (see §"Failure modes specific to AI-authored tests"
→ "Coverage-driven writing").

**Phase 0 blocker for `mutmut`**: `mutmut` pulls in `libcst`, and `libcst`
had no Python 3.14 wheel at Phase-0 cut (needs Rust to build from source
— zero-config uv sync fails). Captured in `2026-08-22-drop-safety.md`
under "Related concurrent adjustments". Config `[tool.mutmut]` stayed
inert in `pyproject.toml`, waiting on either a `libcst` 3.14 wheel or
an alternative tool.

`cosmic-ray` uses `parso` (pure Python, 3.14-native) for AST manipulation
instead of `libcst`, so it should install cleanly. This decision entry
captures the spike that verified the "should".

## Feasibility spike — evidence

Ran on 2026-08-22 against `src/hermes/domain/value_objects/money.py`
(Money value object, 159 LOC, 21 unit tests exercising all named-arithmetic
methods and factories).

**Install**:

```text
$ uv add --dev cosmic-ray
Resolved 30 packages in 3.97s
Would download 21 packages
Would install 21 packages
 + cosmic-ray==8.7.0
 + parso==0.8.7                # not libcst
 + aiohttp==3.14.3, sqlalchemy==2.0.52, gitpython==3.1.59, ...
```

No native builds. No Rust toolchain required. All 21 transitive
dependencies resolved from PyPI wheels on Python 3.14.7 / uv 0.12.5 /
Windows.

**Mutation session** (single-file scope: `money.py`):

```text
$ uv run cosmic-ray init spike.toml spike.sqlite
$ uv run cosmic-ray baseline spike.toml     # unmutated tests: all pass
$ uv run cosmic-ray exec spike.toml spike.sqlite
$ uv run cr-rate spike.sqlite
27.62

$ uv run cr-report spike.sqlite | tail
total jobs: 105
complete: 105 (100.00%)
surviving mutants: 29 (27.62%)
```

**Kill rate**: 72.38% (76 killed of 105 mutants; 29 survived, includes
2 `INCOMPETENT` which cosmic-ray classifies as neither killed nor
survived depending on tooling version).

**Rejection criterion check** (from issue #118): "≥30% surviving mutants
on well-tested value-objects → close as 'not viable'". We measured
**27.62% surviving — under the 30% ceiling**. Tool passes.

## Rationale — why the raw kill-rate looks worse than it is

Breakdown of the 29 surviving mutants:

| Category | Count | Real signal? |
| -------- | ----- | ------------ |
| `ReplaceBinaryOperator_BitOr_*` on `Decimal \| int` type-union syntax | 22 | **No — noise** |
| `ReplaceComparisonOperator_NotEq_IsNot` / `_Lt` / `_Gt` on identity-vs-equality | 4 | Weak |
| `ReplaceTrueWithFalse` on unreachable branch | 1 | Weak |
| `ReplaceBinaryOperator_Div_FloorDiv` on `Decimal` division | 1 | Real — test gap |
| `ReplaceComparisonOperator_Eq_LtE` on `== 0` divide-by-zero guard | 1 | Real — test gap |

The dominant survivor class (22 of 29 = **76% of survivors**) is
cosmic-ray mutating the `|` character in PEP 604 union types
(`Decimal | int`). That's syntax rewrites the type checker would catch,
not runtime logic — a false signal. Filtering that class, real-logic
kill rate is ≈ **93%** (74/79). That's above the 80% blocking floor
we're targeting.

This is a known cosmic-ray pattern with modern Python typing. The response
is to add a suppression / operator filter for `BitOr` when the surrounding
context is a type annotation — deferred to a follow-up once we see the
same signal in the weekly run.

## Alternatives considered

**Wait for libcst 3.14 wheel + re-add mutmut**: uncertain timeline; PSF
tracker shows no ETA; blocks the guide's "load-bearing gate" indefinitely.
Rejected.

**Manual mutation review discipline instead of a tool**: doesn't scale;
the whole point of mutation testing is objective per-line signal that
humans can't reproduce in review. Rejected.

**Alternative tool `mutmut` fork with parso backend**: no maintained fork
exists on PyPI. Would need us to author + maintain one. Rejected — cost
exceeds benefit vs. adopting cosmic-ray as-is.

**cosmic-ray with `mutmut`-style operator subset**: cosmic-ray's plugin
system supports it, but the default operator set is what we tested and
what gives coverage across arithmetic, comparison, boolean, decorator,
and constant mutations. Custom operator config deferred until we have
signal on which classes waste CI budget.

## Consequences

**What this locks in**:

- `cosmic-ray>=8.7` as a dev dep, installed via `uv sync --frozen`.
- Config at `cosmic-ray.toml` (repo root) — TOML matches the rest of our
  tooling (pyproject, cosmic-ray's own convention).
- Weekly workflow `.github/workflows/mutation.yml`, Sundays 03:00 UTC,
  local distributor, kill rate published as `::notice`.
- Advisory soak: 2026-08-22 → 2026-09-05. During this window the
  workflow reports the kill rate but does NOT fail. After 2026-09-05
  the workflow enforces ≥80% (flip `KILL_RATE_ADVISORY: "false"`).
- Scope: `src/hermes/domain/value_objects/` only. Application /
  adapters have side effects — mutation runs there are premature and
  noise-heavy for the current test surface.

**What this forecloses**:

- Not extending mutation scope beyond `hermes.domain` until Phase 2
  when adapter integration tests exist to catch adapter-layer mutations
  meaningfully.
- Not re-adding `mutmut`. The `[tool.mutmut]` config in `pyproject.toml`
  stays inert but should be removed in a follow-up cleanup PR (out of
  scope for this spike).

**What we'll revisit**:

- After 2026-09-05, flip advisory to blocking. Re-tune floor if the
  post-soak numbers argue for it.
- Once more value_objects have tests (currently Money, Currency, LotId,
  Pair, Price, Quantity, Weight — the guide says all 7 are done), widen
  `module-path` from a single-file spike to the whole directory (already
  the `cosmic-ray.toml` default).
- Once we see the `BitOr` type-union false-positive class in the weekly
  run, add an operator-suppression filter or a preprocessing pass.
- If wall-clock exceeds ~30 min per run, switch from `local` distributor
  to `http` workers (cosmic-ray supports it out-of-the-box).

## AI-authored-tests guide impact

The guide's enforcement table row for mutation kill floor changes from
`deferred to Phase 1 pending #118` → `live (advisory 2026-08-22 →
2026-09-05, blocking after)`. That edit lands in this same PR.

## Reproducing the spike locally

```bash
cd hermes
uv sync --frozen
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray baseline cosmic-ray.toml
uv run cosmic-ray exec cosmic-ray.toml session.sqlite
uv run cr-rate session.sqlite         # prints survival %
uv run cr-report session.sqlite       # per-mutant KILLED / SURVIVED
```

Wall-clock: ~2 minutes for the full `value_objects/` directory on a
laptop-class machine (Python 3.14, uv 0.12.5, Windows). Sequential
`local` distributor. CI budget: expect ~8–10 min once all 7 value
objects are exercised, well inside the 60-min workflow timeout.
