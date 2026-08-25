---
title: Statistical Framework — Swing MVP1
experiment_id: swing-mvp1-v1
pre_registration: pre-registration-v1.md §8
feeds: Spec 9 (LOC estimate), Spec 6 (calibration harness)
date: 2026-08-25
status: DRAFT
---

## Statistical Framework — Swing MVP1

This document specifies the Python implementation plan for every statistical
method committed to in `pre-registration-v1.md §8`, plus the sim-vs-live drift
metrics from SA-5 (Wasserstein, KS). It is the primary input to Spec 9's LOC
estimate and to the calibration harness CI gates.

---

## 1. Python Library Survey

### 1.1 mlfinlab (Hudson & Thames — López de Prado's "official-ish" library)

mlfinlab originated as a direct implementation companion to *Advances in
Financial Machine Learning* (Wiley, 2018). It covers DSR, Combinatorial Purged
Cross-Validation (CPCV), fractional Kelly, and several other López de Prado
constructs at a high level of fidelity.

**Maintenance status: ABANDONED for the OSS tier.** Hudson & Thames converted
mlfinlab to a paid subscription product ("MlFinLab Pro") around 2021. The public
PyPI package `mlfinlab` has not received meaningful updates since mid-2021. The
last public commit on the open repo does not support Python 3.12+, let alone
3.14. The paid tier is priced for institutional clients (~$500–2,000/month
depending on plan) — inappropriate for a solo research project.

**License: BSD-3 for the archived OSS version.** The paid tier has a proprietary
EULA. The OSS version is legally usable but technically dead.

**Dependencies:** numpy, scipy, pandas, scikit-learn, statsmodels — all heavy.
The package does NOT declare upper-bound pins, which creates resolution conflicts
with modern toolchains.

**Assessment: DO NOT USE.** The DSR formula and CPCV algorithm are both short
enough to hand-roll from the primary sources (Bailey & López de Prado 2021,
AFML Ch. 12). The abandoned package would introduce Python 3.14 incompatibility
and a dead dependency. Implementing DSR directly is ~50 LOC; CPCV is ~120 LOC.

### 1.2 alphalens / pyfolio (Quantopian legacy)

Quantopian released `alphalens` (factor analysis) and `pyfolio` (portfolio
analytics) as open-source tools. Both are now under the `quantopian` GitHub org,
which is a community-maintained archive. `pyfolio` covers rolling Sharpe,
drawdown, return attribution, and tear-sheet generation. `alphalens` covers
factor IC, turnover, and forward-return analysis.

**Maintenance status: SLOW MAINTENANCE.** Neither package is actively developed.
pyfolio received a community fork as `pyfolio-reloaded` (pip: `pyfolio-reloaded`)
that supports pandas 2.x. Both the original and the fork are on life support —
open issues accumulate without resolution.

**Python 3.14 compatibility: UNKNOWN.** The pyfolio-reloaded fork tracks
pandas 2.x but no test suite runs against 3.14. The packages use `pd.DataFrame`
heavily and may trigger pandas 2.x deprecation warnings that hermes treats as
errors (see `pyproject.toml filterwarnings`).

**Coverage:** pyfolio covers everything except DSR and CPCV. It does have
rolling Sharpe, bootstrap CI approximations, and a Sharpe t-test. alphalens is
factor-model specific — irrelevant for hermes swing strategy (no multi-factor
alpha scoring).

**Assessment: DO NOT USE as a direct dependency.** The bootstrap CI and rolling
Sharpe implementations are instructive as reference code but should be
hand-rolled in hermes to avoid a fragile transitive dependency. pyfolio's
`timeseries.sharpe_ratio()` source can serve as a cross-check during development.

### 1.3 arch (Kevin Sheppard — University of Oxford)

`arch` is the reference Python library for financial time-series econometrics:
GARCH, HAR, EGARCH, and — critically — block bootstrap methods. The
`arch.bootstrap` submodule implements the circular block bootstrap, stationary
bootstrap, and Moving Block Bootstrap (MBB), all with seeding support.

**Maintenance status: ACTIVELY MAINTAINED.** Kevin Sheppard (Oxford, MFE
faculty) maintains it personally with regular releases. The 2024–2025 releases
track Python 3.11–3.13. No 3.14 wheel exists yet (as of 2026-08), but the
package is pure Python at the bootstrap layer — no Cython that would block
3.14 compatibility. A temporary source install or 3.13 compat mode will work.

**License: NCSA (BSD-style). Fully permissive, no commercial restrictions.**

**Dependencies:** numpy, scipy, pandas. No exotic deps. Wheel size is small.

**What it gives hermes:**

- `arch.bootstrap.CircularBlockBootstrap` — direct implementation of the 30-day
  block bootstrap required by pre-registration §8.2, preserving autocorrelation.
- `arch.bootstrap.StationaryBootstrap` — alternative if block length is
  data-driven rather than fixed.
- Seeding via `random_state` parameter — satisfies reproducibility requirement.
- GARCH/HAR models for volatility forecasting (MVP2 use; not MVP1).

**Assessment: INCLUDE.** This is the cleanest path to a reproducible block
bootstrap CI. The alternative is hand-rolling `numpy`-based block resampling
(~80 LOC), which is feasible but forgoes the academic validation of a
well-tested library. Given that the bootstrap CI is a pre-registered primary
statistic, using a maintained reference implementation reduces the risk of
subtle bugs (off-by-one in block boundaries, non-stationary resampling, etc.).

### 1.4 scipy.stats (Bonferroni via statsmodels.stats.multitest, KS via ks_2samp)

`scipy` is already an implicit dependency through `numpy` and will be a direct
one for the statistical framework.

**Relevant functions:**

- `scipy.stats.ks_2samp(a, b)` — two-sample Kolmogorov-Smirnov test. Used in
  SA-5 (M-07) for partial fill distribution calibration. Returns `(statistic,
  pvalue)`. O(n log n).
- `scipy.stats.wasserstein_distance(u, v)` — 1D earth-mover / Wasserstein-1
  distance. Used in SA-5 (M-08) for partial fill quantity distribution drift.
  Returns a scalar in the same units as the input.
- `scipy.stats.norm.cdf` — needed for the DSR formula's `Ψ(·)` term (standard
  normal CDF).
- `scipy.stats.skew`, `scipy.stats.kurtosis` — skewness and excess kurtosis for
  DSR's γ̂₃, γ̂₄ inputs.

**Bonferroni correction:** `scipy.stats` does NOT include `multipletests`.
That function lives in **`statsmodels.stats.multitest`**. Hermes must add
`statsmodels` as a dependency or hand-roll Bonferroni (trivial: adjusted
α = α_FWER / N). Hand-rolling is recommended to keep deps minimal — Bonferroni
is literally one line.

**Maintenance status: EXCELLENT.** SciPy is a cornerstone of the scientific
Python stack, actively maintained, Python 3.14 wheels published.

**License: BSD-3. Permissive.**

**Assessment: INCLUDE (already implicit). Add explicit pin.**

### 1.5 POT — Python Optimal Transport (for Wasserstein distance)

`POT` (Python Optimal Transport, pip: `pot`) provides general-purpose optimal
transport including Wasserstein-p distances in arbitrary dimensions, the
Sinkhorn algorithm, and Earth Mover's Distance with regularization.

**For hermes' use case (1D Wasserstein on fill-price bps distributions),
`scipy.stats.wasserstein_distance` is sufficient and requires no additional
dependency.** `POT` adds value only when comparing multi-dimensional
distributions (e.g., joint (price, quantity) fill distributions) or when a
regularized / approximate transport is needed for performance.

**Maintenance status: ACTIVE.** INRIA-backed, regular releases.

**License: MIT.**

**Assessment: DO NOT ADD for MVP1.** `scipy.stats.wasserstein_distance` covers
the 1D use case completely. `POT` is a YAGNI addition. Flag for MVP2 if joint
distribution comparison becomes necessary.

### 1.6 Other libraries assessed and rejected

**Backtrader / Zipline:** Both are backtest frameworks, not statistical
libraries. Zipline is effectively dead (last active development 2022).
Backtrader is community-maintained without a 3.13+ roadmap. Neither adds
statistical capability beyond what `arch` + `scipy` provide. DO NOT ADD.

**statsmodels:** The `statsmodels` package covers a huge surface area
(regression, time series, HAR). hermes does not need the bulk. The only
relevant function is `multipletests` for Bonferroni, which is hand-rollable.
DO NOT ADD as a dependency — hand-roll the one-liner instead.

**quantstats:** A reporting-layer wrapper around pyfolio-style analytics. Same
maintenance concerns as pyfolio. DO NOT ADD.

**numba / Cython:** Performance tools, not statistical libraries. SA-10 already
assessed: YAGNI at 15 trades/month. DO NOT ADD.

---

## 2. Recommended Library Stack for Hermes

Minimal, pip-installable, Python 3.14-compatible:

```toml
# Add to [dependency-groups] stats in pyproject.toml
# (or embed in the main [project.dependencies] if calibration runs in prod)

"scipy>=1.14,<2",        # ks_2samp, wasserstein_distance, norm.cdf, skew, kurtosis
"numpy>=2.1,<3",         # array ops for DSR, bootstrap resampling
"arch>=7.2,<8",          # CircularBlockBootstrap (block bootstrap CI)
```

**NOT added:**

- `mlfinlab` — abandoned, Python 3.14 incompatible
- `pyfolio-reloaded` — marginal gain vs fragile dep
- `POT` — 1D Wasserstein already in scipy
- `statsmodels` — Bonferroni hand-rolled (1 line)

**numpy** is almost certainly already present transitively; pin it explicitly
for reproducibility.

**arch** is the only new production dependency added. Its footprint is small
(~500 KB wheel) and its circular block bootstrap is the cleanest implementation
of the pre-registered method.

**For calibration-only code** (runs in shadow/backtest modes, not in the live
path), these can be confined to a `[dependency-groups] stats` optional group if
the team wants to keep the production runtime lean. The live order-routing path
must never import from `scipy` or `arch`.

---

## 3. Integration Points with Hermes Testing Infrastructure (SA-10 CI Pyramid)

SA-10 defines the CI pyramid:

- **Unit + property**: every PR, target < 60s
- **Chaos + replay regression**: nightly
- **Calibration harness + mutation**: weekly

The statistical framework slots into each tier:

| Tier | Statistical code exercised | Test type |
| ------ | --------------------------- | ----------- |
| Unit (every PR) | `compute_dsr()`, `compute_bootstrap_ci()`, `ks_metric()`, `wasserstein_metric()`, `bonferroni_adjust()` — all as pure functions | `pytest` unit tests with fixed seeds |
| Property (every PR) | DSR monotonicity (more trials → lower DSR), bootstrap CI width vs. sample size, KS D-statistic in [0,1], Wasserstein ≥ 0 | `hypothesis @given` |
| Nightly | Walk-forward + CPCV over synthetic 3-year series (fast replay fixture) | Replay regression with `syrupy` snapshot |
| Weekly | Full bootstrap with B=10,000 on real backtest returns (slow, ~10s per run) | `pytest -m slow` + mutation |

**Key design rule:** all statistical functions are pure — they take `numpy`
arrays and return dataclasses. No SQLite reads, no HTTP. The application layer
fetches data and passes it as arrays. This keeps the statistical module 100%
synchronous and testable in isolation without any async plumbing.

**asyncio integration:** SA-10 §2.1 specifies that blocking scipy/numpy calls
should use `asyncio.to_thread()` when called from the async calibration loop.
The statistical functions are designed to be safe for offloading this way because
they hold no shared state.

---

## 4. Concrete API Sketches

All functions live in `src/hermes/domain/statistics/` (pure domain module,
no I/O). The import-linter contract (`domain-no-async-or-network`) is satisfied
because these are synchronous pure functions.

### 4.1 Deflated Sharpe Ratio (DSR)

**Source:** Bailey, D. H. & López de Prado, M. (2021). "The Deflated Sharpe
Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality."
*Journal of Portfolio Management* 40(5), 94–107.
DOI: <https://doi.org/10.3905/jpm.2014.40.5.094>

Also: López de Prado, M. (2018). *Advances in Financial Machine Learning*,
Ch. 8 (SR* derivation) and Ch. 6 (DSR formula with non-normality corrections).
Wiley. ISBN 978-1-119-48208-6.

**Formula:**

```text
DSR = Ψ(ŜR̂ - SR* · √((1 - γ̂₃·ŜR̂ + (γ̂₄-1)/4·ŜR̂²) · ln(N)/(T-1)))
```

where:

- `ŜR̂` = observed annualised Sharpe ratio (OOS returns series)
- `SR*` = benchmark Sharpe (set to 0 for "is the strategy positive?"; or to the
  median Sharpe across all N trials for the "is this trial the best?" test)
- `N` = number of independent strategy configurations tried (logged in lab notebook)
- `T` = number of return observations in the OOS series
- `γ̂₃` = skewness of the OOS return series
- `γ̂₄` = excess kurtosis of the OOS return series
- `Ψ(·)` = standard normal CDF (`scipy.stats.norm.cdf`)

**Intuition:** as N increases (more strategy trials), the benchmark SR* scales
up by `√(ln(N)/(T-1))`, requiring a proportionally higher observed Sharpe to
still achieve the pre-registered threshold of DSR > 0.7.

```python
from dataclasses import dataclass
import numpy as np
from scipy.stats import norm, skew, kurtosis


@dataclass(frozen=True)
class DSRResult:
    dsr: float           # in [0, 1]; pre-registration threshold: > 0.7
    observed_sr: float   # annualised Sharpe on OOS series
    sr_star: float       # benchmark Sharpe used
    n_trials: int        # total strategy configurations tried
    t_obs: int           # number of OOS observations
    skewness: float
    excess_kurtosis: float


def compute_dsr(
    oos_returns: np.ndarray,   # daily (or bar) return series, NOT annualised
    n_trials: int,             # total configurations tried (from lab notebook)
    sr_star: float = 0.0,      # benchmark; 0.0 = "is strategy positive?"
    periods_per_year: int = 252,  # 252 for daily; 365*6 for 4H crypto bars
) -> DSRResult:
    """
    Compute Deflated Sharpe Ratio per Bailey & López de Prado (2021).

    Expected performance: <1ms for T up to 10,000 observations.
    """
    t = len(oos_returns)
    if t < 2:
        raise ValueError(f"Need at least 2 observations, got {t}")

    mean_r = float(np.mean(oos_returns))
    std_r = float(np.std(oos_returns, ddof=1))
    if std_r == 0.0:
        raise ValueError("Return series has zero variance — cannot compute Sharpe.")

    sr_hat = mean_r / std_r * np.sqrt(periods_per_year)
    gamma3 = float(skew(oos_returns))
    gamma4 = float(kurtosis(oos_returns, fisher=True))  # excess kurtosis

    # Non-normality and multi-trial correction term
    correction = sr_star * np.sqrt(
        (1 - gamma3 * sr_hat + (gamma4 - 1) / 4 * sr_hat**2)
        * np.log(n_trials)
        / (t - 1)
    )
    dsr_value = float(norm.cdf(sr_hat - correction))

    return DSRResult(
        dsr=dsr_value,
        observed_sr=sr_hat,
        sr_star=sr_star,
        n_trials=n_trials,
        t_obs=t,
        skewness=gamma3,
        excess_kurtosis=gamma4,
    )
```

**Expected performance:** < 1 ms for T ≤ 10,000. Entirely CPU-bound on a
few numpy vector ops. No blocking I/O.

---

### 4.2 Bootstrap CI on OOS Sharpe (Block Bootstrap)

**Pre-registration commitment:** 10,000 resamples, circular block bootstrap,
30-day blocks, 95% CI lower bound > 0 required for H1.

```python
from dataclasses import dataclass
import numpy as np
from arch.bootstrap import CircularBlockBootstrap


@dataclass(frozen=True)
class BootstrapCIResult:
    lower_95: float      # 2.5th percentile of bootstrap Sharpe distribution
    upper_95: float      # 97.5th percentile
    median: float
    n_resamples: int
    block_size: int


def compute_bootstrap_sharpe_ci(
    oos_returns: np.ndarray,
    block_size: int = 30,          # 30-day blocks per pre-registration §8.2
    n_resamples: int = 10_000,     # per pre-registration §8.2
    periods_per_year: int = 252,
    random_state: int = 42,        # fixed seed for reproducibility
) -> BootstrapCIResult:
    """
    Circular block bootstrap CI on annualised Sharpe ratio.

    Uses arch.bootstrap.CircularBlockBootstrap to preserve autocorrelation
    structure within blocks. The circular variant treats the series as
    periodic (wraps end to start), avoiding edge effects.

    Expected performance: ~3-8s for 10,000 resamples on 500 observations.
    Call via asyncio.to_thread() in the async calibration loop.
    """
    bs = CircularBlockBootstrap(block_size, oos_returns, random_state=random_state)

    def _sharpe(x: np.ndarray) -> float:
        x = x.squeeze()
        std = float(np.std(x, ddof=1))
        if std == 0.0:
            return 0.0
        return float(np.mean(x) / std * np.sqrt(periods_per_year))

    bootstrap_sharpes = np.array([
        _sharpe(data[0])
        for data, _ in bs.bootstrap(n_resamples)
    ])

    return BootstrapCIResult(
        lower_95=float(np.percentile(bootstrap_sharpes, 2.5)),
        upper_95=float(np.percentile(bootstrap_sharpes, 97.5)),
        median=float(np.median(bootstrap_sharpes)),
        n_resamples=n_resamples,
        block_size=block_size,
    )
```

**Expected performance:** 3–8 s for B=10,000, T=500 returns (pure numpy loop
over resamples). For CI computation run at certification time (not hot path),
this is acceptable. Wrap in `asyncio.to_thread()` per SA-10 §2.1 if called
from the async calibration loop.

---

### 4.3 Walk-Forward Analysis (Expanding Window)

**Pre-registration commitment:** expanding window, 6-month retrain, 3-month
test, applied to train+val ONLY.

```python
from dataclasses import dataclass
from collections.abc import Callable
import numpy as np


@dataclass(frozen=True)
class WalkForwardFold:
    fold_idx: int
    train_slice: slice
    test_slice: slice
    test_sharpe: float


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[WalkForwardFold]
    median_test_sharpe: float
    p10_test_sharpe: float   # distribution left tail
    all_test_sharpes: np.ndarray


def walk_forward_expanding(
    returns: np.ndarray,               # full train+val return series (daily bars)
    retrain_every: int = 126,          # ~6 months of trading days
    test_window: int = 63,             # ~3 months of trading days
    min_train_obs: int = 252,          # minimum 1 year of history before first test
    sharpe_fn: Callable[[np.ndarray], float] | None = None,
    periods_per_year: int = 252,
) -> WalkForwardResult:
    """
    Expanding-window walk-forward analysis per pre-registration §8.3.

    NEVER called on OOS data — the caller is responsible for passing only
    the train+val slice. This function has no data-split awareness; the
    OOS wall is enforced at the application layer.

    Expected performance: <10ms for 1,500 obs (54 months daily).
    """
    if sharpe_fn is None:
        def sharpe_fn(x: np.ndarray) -> float:
            std = float(np.std(x, ddof=1))
            return 0.0 if std == 0.0 else float(np.mean(x) / std * np.sqrt(periods_per_year))

    n = len(returns)
    folds: list[WalkForwardFold] = []
    fold_idx = 0

    # First test window starts after min_train_obs
    test_start = min_train_obs
    while test_start + test_window <= n:
        test_end = test_start + test_window
        test_sharpe = sharpe_fn(returns[test_start:test_end])
        folds.append(WalkForwardFold(
            fold_idx=fold_idx,
            train_slice=slice(0, test_start),
            test_slice=slice(test_start, test_end),
            test_sharpe=test_sharpe,
        ))
        fold_idx += 1
        test_start += retrain_every

    sharpes = np.array([f.test_sharpe for f in folds])
    return WalkForwardResult(
        folds=folds,
        median_test_sharpe=float(np.median(sharpes)),
        p10_test_sharpe=float(np.percentile(sharpes, 10)),
        all_test_sharpes=sharpes,
    )
```

**Expected performance:** < 10 ms for 1,500 observations (54 months daily).
Pure Python loop, no heavy compute.

---

### 4.4 Purged Combinatorial Cross-Validation (CPCV)

**Pre-registration commitment:** 5-fold, 5-day purge + 3-day embargo.

**Source:** López de Prado, M. (2018). *Advances in Financial Machine Learning*,
Ch. 12. Wiley. The CPCV algorithm is described in full detail there.

```python
from dataclasses import dataclass
from itertools import combinations
import numpy as np


@dataclass(frozen=True)
class CPCVFold:
    fold_idx: int
    train_indices: np.ndarray
    test_indices: np.ndarray
    test_sharpe: float


@dataclass(frozen=True)
class CPCVResult:
    folds: list[CPCVFold]
    n_folds: int
    n_test_combinations: int   # C(n_folds, k_test) paths
    median_test_sharpe: float
    p10_test_sharpe: float
    sharpe_distribution: np.ndarray


def purged_cpcv(
    returns: np.ndarray,
    n_folds: int = 5,            # per pre-registration §8.3
    k_test: int = 2,             # number of folds held out per combination
    purge_days: int = 5,         # per pre-registration §8.3
    embargo_days: int = 3,       # per pre-registration §8.3
    sharpe_fn: Callable[[np.ndarray], float] | None = None,
    periods_per_year: int = 252,
) -> CPCVResult:
    """
    Purged Combinatorial Cross-Validation per López de Prado (2018) Ch. 12.

    Splits returns into n_folds groups; for each C(n_folds, k_test) combination
    of test folds, removes purge_days observations around fold boundaries and
    embargo_days observations after each test fold to prevent leakage.

    With n_folds=5, k_test=2: C(5,2) = 10 test paths.
    With n_folds=5, k_test=1: 5 test paths (equivalent to walk-forward).

    Expected performance: <50ms for 1,500 obs, 5-fold, 10 combinations.
    """
    if sharpe_fn is None:
        def sharpe_fn(x: np.ndarray) -> float:
            std = float(np.std(x, ddof=1))
            return 0.0 if std == 0.0 else float(np.mean(x) / std * np.sqrt(periods_per_year))

    n = len(returns)
    fold_size = n // n_folds
    # Fold boundaries: [0, fold_size, 2*fold_size, ..., n]
    boundaries = [i * fold_size for i in range(n_folds + 1)]
    boundaries[-1] = n  # ensure last fold absorbs remainder

    all_folds: list[CPCVFold] = []
    fold_combo_idx = 0

    for test_fold_indices in combinations(range(n_folds), k_test):
        test_indices_raw: list[int] = []
        for fi in test_fold_indices:
            test_indices_raw.extend(range(boundaries[fi], boundaries[fi + 1]))

        test_set = set(test_indices_raw)
        train_indices_list: list[int] = []

        for i in range(n):
            if i in test_set:
                continue
            # Apply purge: skip observations within purge_days of any test boundary
            near_boundary = False
            for fi in test_fold_indices:
                lb, ub = boundaries[fi], boundaries[fi + 1]
                if abs(i - lb) < purge_days or abs(i - ub) < purge_days:
                    near_boundary = True
                    break
            if near_boundary:
                continue
            # Apply embargo: skip observations within embargo_days AFTER a test fold
            embargoed = False
            for fi in test_fold_indices:
                ub = boundaries[fi + 1]
                if 0 <= i - ub < embargo_days:
                    embargoed = True
                    break
            if not embargoed:
                train_indices_list.append(i)

        train_idx = np.array(train_indices_list)
        test_idx = np.array(sorted(test_indices_raw))

        if len(train_idx) < 30 or len(test_idx) < 10:
            continue  # insufficient data — skip fold

        test_sharpe = sharpe_fn(returns[test_idx])
        all_folds.append(CPCVFold(
            fold_idx=fold_combo_idx,
            train_indices=train_idx,
            test_indices=test_idx,
            test_sharpe=test_sharpe,
        ))
        fold_combo_idx += 1

    sharpes = np.array([f.test_sharpe for f in all_folds])
    return CPCVResult(
        folds=all_folds,
        n_folds=n_folds,
        n_test_combinations=len(all_folds),
        median_test_sharpe=float(np.median(sharpes)),
        p10_test_sharpe=float(np.percentile(sharpes, 10)),
        sharpe_distribution=sharpes,
    )
```

**Expected performance:** < 50 ms for T=1,500, C(5,2)=10 paths. The inner
loop is O(n * C(n_folds, k_test)) which is tractable for the train+val period.

---

### 4.5 Multiple-Testing Correction (Bonferroni)

**Pre-registration commitment:** Bonferroni applied if any secondary metric is
added post-v1 signing; every added test logged in lab notebook.

Bonferroni is a one-liner. No `statsmodels` needed:

```python
def bonferroni_adjusted_alpha(
    family_wise_alpha: float,   # desired FWER (e.g. 0.05)
    n_tests: int,               # total number of hypothesis tests in the family
) -> float:
    """
    Bonferroni correction: adjusted alpha per test = FWER / n_tests.

    Usage: if any individual test's p-value < bonferroni_adjusted_alpha(0.05, N),
    it is significant at the family-wise 0.05 level.

    Conservative. For N=5 secondary metrics: threshold = 0.01 per test.
    """
    if n_tests <= 0:
        raise ValueError(f"n_tests must be positive, got {n_tests}")
    return family_wise_alpha / n_tests
```

**Expected performance:** nanoseconds. Pure arithmetic.

---

### 4.6 KS Test (sim-vs-live fill distribution)

From `scipy.stats.ks_2samp`, as specified in SA-5 §3.2:

```python
from dataclasses import dataclass
from scipy.stats import ks_2samp


@dataclass(frozen=True)
class KSResult:
    statistic: float   # D-statistic in [0, 1]
    p_value: float
    status: str        # "OK" | "WARN" | "BLOCK"


def compute_ks_metric(
    sim_bps: np.ndarray,
    real_bps: np.ndarray,
    d_warn: float = 0.10,     # D > 0.10 → WARN
    d_block: float = 0.15,    # D > 0.15 AND p < 0.05 → BLOCK (SA-5 §3.2)
    alpha: float = 0.05,
) -> KSResult:
    """
    Two-sample KS test on fill-price divergence distributions (in bps).

    SA-5 §3.2: alert if stat > 0.15 AND p_value < 0.05.
    Expected performance: O(n log n), <1ms for n < 10,000.
    """
    stat, p = ks_2samp(sim_bps, real_bps)
    if stat > d_block and p < alpha:
        status = "BLOCK"
    elif stat > d_warn:
        status = "WARN"
    else:
        status = "OK"
    return KSResult(statistic=float(stat), p_value=float(p), status=status)
```

**Expected performance:** O(n log n), < 1 ms for n ≤ 10,000.

---

### 4.7 Wasserstein Distance (sim-vs-live drift)

From `scipy.stats.wasserstein_distance`, as specified in SA-5 §3.2:

```python
from dataclasses import dataclass
from scipy.stats import wasserstein_distance


@dataclass(frozen=True)
class WassersteinResult:
    distance_bps: float   # earth-mover distance in bps units
    status: str           # "OK" | "WARN" | "BLOCK"


def compute_wasserstein_metric(
    sim_bps: np.ndarray,
    real_bps: np.ndarray,
    warn_threshold_bps: float = 10.0,    # > 10 bps → WARN
    block_threshold_bps: float = 20.0,   # > 20 bps → BLOCK (SA-5 §3.2)
) -> WassersteinResult:
    """
    Wasserstein-1 distance between sim and real fill-price bps distributions.

    SA-5 §3.2: alert if w1 > 20 bps (mass shift to match distributions).
    Complements KS: KS is shape-sensitive, Wasserstein captures magnitude.
    Expected performance: O(n log n), <2ms for n < 10,000.
    """
    w1 = float(wasserstein_distance(sim_bps, real_bps))
    if w1 > block_threshold_bps:
        status = "BLOCK"
    elif w1 > warn_threshold_bps:
        status = "WARN"
    else:
        status = "OK"
    return WassersteinResult(distance_bps=w1, status=status)
```

**Expected performance:** O(n log n), < 2 ms for n ≤ 10,000.

---

## 5. Reproducibility Considerations

### 5.1 RNG Seeds

Every function that uses randomness accepts an explicit `random_state: int`
parameter. The default is `42` as a convention but the caller MUST pass the
seed explicitly in production paths — no implicit global RNG state.

```python
# Good — explicit, logged, reproducible
result = compute_bootstrap_sharpe_ci(oos_returns, random_state=42)

# Bad — seed invisible to caller, results not reproducible across runs
bs = CircularBlockBootstrap(30, oos_returns)  # no seed
```

The seed used for certification-phase bootstrap is recorded in the lab notebook
alongside the result. Regenerating the CI with the same seed on the same data
must produce bit-identical output.

### 5.2 Determinism

`arch.bootstrap.CircularBlockBootstrap` with a fixed `random_state` is
deterministic. It uses numpy's `np.random.default_rng(seed)` internally —
the new-style Generator API that is reproducible across platforms and numpy
versions (unlike the legacy `np.random.seed()` global state).

`scipy.stats` functions (`ks_2samp`, `wasserstein_distance`) are deterministic
with no internal randomness — same input always yields same output.

The DSR, walk-forward, and CPCV functions are fully deterministic with no RNG.

### 5.3 Floating-Point Determinism

numpy and scipy use platform BLAS for some operations. For the operations in
this framework (mean, std, percentile, sort-based KS/Wasserstein), the
implementations are pure numpy without BLAS dispatch — results are
reproducible across architectures. The bootstrap inner loop is a simple
`np.mean / np.std` pair, which is also stable.

If cross-platform exact bit reproducibility becomes a requirement (e.g., audit
logs), all intermediate results should be serialised to JSON with sufficient
decimal places (≥ 10) before storage. The certification report format in SA-5
§9.1 already stores metrics as floats — recommend 10 decimal places.

### 5.4 Version Pinning

The exact versions of `scipy`, `numpy`, and `arch` used at certification must
be captured in the data manifest (`data-manifest.json`) alongside the dataset
SHA256 fingerprints. The `pip freeze` output at certification time is the
reproducibility artefact.

```json
// data-manifest.json (addition)
"statistical_env": {
  "scipy": "1.14.x",
  "numpy": "2.1.x",
  "arch": "7.2.x",
  "python": "3.14.x",
  "captured_at": "2027-01-01T00:00:00Z"
}
```

---

## 6. DSR Formula with Full Citation

### Formula

```text
DSR = Ψ( ŜR̂ - SR* · √( (1 - γ̂₃·ŜR̂ + (γ̂₄-1)/4·ŜR̂²) · ln(N)/(T-1) ) )
```

| Symbol | Meaning |
| -------- | --------- |
| `Ψ(·)` | Standard normal CDF (`scipy.stats.norm.cdf`) |
| `ŜR̂` | Observed annualised Sharpe ratio on OOS period |
| `SR*` | Benchmark Sharpe; set to 0 for the primary test ("is the strategy positive?") |
| `N` | Total number of strategy configurations tested (logged in lab notebook) |
| `T` | Number of return observations |
| `γ̂₃` | Sample skewness of return series (`scipy.stats.skew`) |
| `γ̂₄` | Sample excess kurtosis (`scipy.stats.kurtosis(fisher=True)`) |

### Sources

**Primary paper:** Bailey, D. H. & López de Prado, M. (2021). "The Deflated
Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and
Non-Normality." *Journal of Portfolio Management* 40(5), 94–107.
DOI: <https://doi.org/10.3905/jpm.2014.40.5.094>

Note: the DOI resolves to the 2014 publication date — the paper first appeared
as a working paper in 2014 and was reprinted/expanded in the 2021 volume. Both
dates appear in different citation styles. The DOI above is the canonical one.

**Book derivation:** López de Prado, M. (2018). *Advances in Financial Machine
Learning*, Ch. 8 (SR* as benchmark Sharpe across trials), Ch. 6 (non-normality
corrections). Wiley. ISBN 978-1-119-48208-6.

**Also:** López de Prado, M. (2020). *Machine Learning for Asset Managers*,
Ch. 6 ("Testing set overfitting"). Cambridge University Press.
DOI: <https://doi.org/10.1017/9781108883658>

---

## 7. Library Gaps Requiring Hand-Rolled Implementation

| Method | Gap | Hand-rolled effort |
| -------- | ----- | ------------------- |
| DSR | No maintained library. mlfinlab abandoned. | ~50 LOC (see §4.1) |
| Walk-forward (expanding) | No library matches hermes' exact shape. Backtrader/Zipline have WF but are dead. | ~60 LOC (see §4.3) |
| CPCV | mlfinlab had it; abandoned. No alternative. | ~120 LOC (see §4.4) |
| Bonferroni correction | statsmodels has it but the full dep is not worth adding. | 1 LOC (see §4.5) |
| Block bootstrap CI | `arch` covers this. | 0 LOC |
| KS test | `scipy` covers this. | 0 LOC |
| Wasserstein-1 | `scipy` covers this. | 0 LOC |

**Total hand-rolled LOC for statistical functions: ~231 LOC** (excluding
tests and docstrings).

Flags:

- DSR gap is the most significant risk. The formula in Bailey & López de Prado
  is clear but the non-normality correction term (skewness/kurtosis under the
  radical) is subtle. The implementation in §4.1 should be validated against
  the worked example in the paper before certification.
- CPCV boundary logic (purge + embargo) is fiddly. The implementation in §4.4
  must have property tests verifying that (a) no test observation appears in
  training data, (b) the purge gap is honoured exactly.

---

## 8. Rough LOC Estimate (for Spec 9)

| Module | LOC (production) | LOC (tests) |
| -------- | ------------------ | ------------- |
| `domain/statistics/dsr.py` | 50 | 80 |
| `domain/statistics/bootstrap_ci.py` | 45 | 60 |
| `domain/statistics/walk_forward.py` | 60 | 70 |
| `domain/statistics/cpcv.py` | 120 | 150 |
| `domain/statistics/corrections.py` (Bonferroni) | 15 | 25 |
| `domain/statistics/calibration_metrics.py` (KS + Wasserstein) | 60 | 90 |
| `domain/statistics/__init__.py` (re-exports) | 10 | 0 |
| **Total** | **360** | **475** |

**Grand total: ~835 LOC** (production + tests combined).

Assumptions:

- Tests follow hermes convention: one `pytest` unit test file per module,
  `hypothesis @given` property tests for mathematical invariants.
- CPCV test suite includes a property test asserting no leakage between
  train/test sets (critical correctness invariant).
- Bootstrap CI tests use a fixed seed and compare against a `syrupy` golden
  snapshot to catch numerical regressions.
- LOC counts exclude blank lines and comments; they count executable statements.

---

## Appendix: Abandoned / Flagged Libraries Summary

| Library | Status | Decision |
| --------- | -------- | ---------- |
| mlfinlab | ABANDONED — no Python 3.12+ support; paid pro tier required for maintenance | DO NOT USE |
| pyfolio / pyfolio-reloaded | SLOW MAINTENANCE — pandas 2.x compat uncertain; DeprecationWarning risk | DO NOT USE |
| alphalens | ARCHIVED — Quantopian shutdown; factor-specific, not relevant | DO NOT USE |
| Zipline | DEAD — last active 2022, no 3.12+ support | DO NOT USE |
| Backtrader | COMMUNITY-MAINTAINED — no testing roadmap, no 3.13+ coverage | DO NOT USE |
| statsmodels | ACTIVE but oversized — only `multipletests` is relevant; hand-roll instead | DO NOT ADD |
| POT (Python Optimal Transport) | ACTIVE — but 1D Wasserstein already in scipy | YAGNI for MVP1 |
| quantstats | ACTIVE wrapper — same maintenance concerns as pyfolio; not worth the dep | DO NOT USE |
