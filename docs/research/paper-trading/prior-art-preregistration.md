# Prior Art: Pre-Registration in Quantitative / Algorithmic Finance

> Research memo — hermes project, 2026-08-25
> Scope: What formalized pre-registration or equivalent discipline mechanisms exist in quant finance before hermes' pre-registration-v1? What lessons apply?

---

## Executive Summary

Formal pre-registration — writing a falsifiable, cryptographically-bound hypothesis before seeing outcome data — does **not exist as a named practice** in quantitative finance at the practitioner level. What exists is a constellation of weaker, related mechanisms:

- **Statistical post-hoc correction** (Deflated Sharpe Ratio, multiple-testing adjustments) — López de Prado / Bailey / Harvey
- **Platform-enforced temporal separation** (QuantConnect Alpha Streams, Numerai)
- **Financial pre-commitment via staking** (Numerai NMR burning)
- **Walk-forward + purged CV methodology** — replaces pre-registration with structural data discipline
- **Academic pre-analysis plans** — growing in RCT economics, barely touched in quantitative finance
- **Hypothesis-first practitioner movement** — nascent blogosphere movement, no institution behind it

hermes' pre-registration-v1 is the **first documented attempt to apply the full clinical-trial pre-registration model** (frozen hypothesis, fixed universe, cryptographic hash, immutable kill criteria, triple-role separation) **to a live retail crypto strategy**. It is not a copy of any existing mechanism — it synthesizes from clinical trials, physics experiments, and academic OSF/AEA practices into an operational trading protocol.

---

## 1. Inventory of Existing Mechanisms

### 1.1 QuantConnect Alpha Streams

Confidence: H

QuantConnect Alpha Streams is a marketplace where quant developers license algorithms to hedge funds. It operates an enforced IS/OOS separation that is the closest platform-level mechanism to pre-registration in commercial quant:

- **5-year mandatory backtest window** — all submissions must include at least 5 years of backtesting history covering the most recent 5 years. This is a minimum data-quality gate, not a pre-specification requirement.
- **Independent third-party OOS verification** — QuantConnect runs the submitted algorithm forward in its production environment on live market data. Insights generated out-of-sample are marked as such in the marketplace and displayed separately from backtest performance.
- **Anti-overfitting review** — submissions are rejected for obvious look-ahead bias (e.g., hardcoded dates derived from known historical events).
- **Live trading obligation** — licensed alphas must trade on deployment day; investors expect immediate live operation.
- **No pre-registration of hypotheses** — there is no mechanism requiring the developer to state the economic rationale, hypothesis, or kill criteria before backtesting begins. The discipline is structural (temporal separation) not epistemological (pre-specification).

**Source:** [QuantConnect Alpha Streams — 9 Ways to Get Your Alpha Rejected](https://www.quantconnect.com/forum/discussion/6737/9-ways-to-get-your-alpha-rejected/), [Alpha Streams Overview](https://www.quantconnect.com/docs/alpha-streams/overview)

**Lesson for hermes:** QC's temporal separation (5-year backtest → live OOS) is the weaker version of what hermes does. hermes' OOS window is code-enforced (physically refuses reads outside certification mode) rather than review-enforced by a third party. More robust.

---

### 1.2 Numerai Tournament

Confidence: H

Numerai (founded 2015 by Richard Craib) is the most sophisticated pre-commitment mechanism in quantitative finance. It combines:

Structural OOS via live data release:

- Each round (Tuesday–Saturday), Numerai releases live features representing the current state of the stock market — features without labels. You generate predictions using only those features and submit within a 1-hour window.
- Scoring occurs over 20 business days (~1 month), measuring actual realized returns. You cannot see the labels before submitting.
- 24 overlapping rounds run simultaneously. The scoring timeline (4 days to first score, ~20 to final) is structurally designed to prevent label peeking.

Financial staking as pre-commitment:

- Participants stake NMR (Numeraire token) on their submitted predictions before outcomes are known.
- Positive correlation with future returns → NMR earned. Negative correlation → NMR burned (permanently destroyed — does not go to Numerai or anyone else).
- The burning mechanism is a financial analogue of pre-registration: you commit capital to the prediction before the outcome is observable, making post-hoc adjustment impossible.
- Empirical result: staked predictions significantly outperform unstaked ones (Sharpe 2.09 vs 1.66), confirming that financial pre-commitment filters noise from genuine signal.

Blockchain cryptographic timestamp (Erasure protocol):

- Numerai uses the Erasure protocol, recording prediction submissions to the blockchain. This provides a tamper-proof timestamp of what was submitted and when — a true cryptographic commitment.

Encrypted training data:

- Training features are obfuscated (true security names hidden), preventing participants from reverse-engineering the data and constructing look-ahead-biased models from public market knowledge.

Limitations vs hermes:

- Numerai's pre-commitment is about *predictions from a trained model* — not about the model's hypothesis or kill criteria.
- No equivalent to: (a) pre-specifying the economic rationale, (b) freezing a kill criterion before any data is seen, (c) a multiple-testing count in a lab notebook.
- Participants can retrain models between rounds, implicitly allowing adaptation. The commitment is per-round, not per-experiment.

**Source:** [Numerai Staking docs](https://docs.numer.ai/numerai-tournament/staking), [Numerai Submissions](https://docs.numer.ai/numerai-tournament/submissions), [Numerai Scoring](https://docs.numer.ai/numerai-tournament/scoring), [Gemini Numerai overview](https://www.gemini.com/cryptopedia/numerai-tournaments)

**What hermes borrows from Numerai:** The blockchain-hash as immutable commitment (hermes does this with SHA256 of the pre-registration document in §16). The financial penalty concept (hermes: live capital at risk with pre-specified kill criteria, not token burning).

---

### 1.3 WorldQuant BRAIN / International Quant Championship (IQC)

Confidence: M

WorldQuant's BRAIN platform enforces some IS/OOS discipline:

- **OOS testing requirement** — alphas are scored only if they qualify for out-of-sample testing, but specific IS/OOS separation methodology is not publicly documented.
- **Registration-first rule** — no alphas submitted before IQC registration count toward scoring, establishing a temporal commitment gate.
- **Scoring formula** — alphas scored on Sharpe ratio, turnover, and a proprietary "fitness" metric. Correlation testing between team-submitted alphas reduces redundancy.
- **No pre-hypothesis requirement** — BRAIN does not require stating the economic rationale before building the alpha. The discipline is purely statistical and temporal.

**Source:** [IQC Guidelines](https://www.worldquant.com/brain/iqc-guidelines/), [WorldQuant BRAIN Launch](https://www.worldquant.com/ideas/worldquant-launches-brain-platform-and-inaugural-global-alphathon-competition/)

**Assessment:** WorldQuant's mechanism is closer to a grading rubric than a pre-registration protocol. Confidence is medium because IQC documentation is sparse on IS/OOS implementation details.

---

### 1.4 Marcos López de Prado — The Framework Builder

Confidence: H

López de Prado is the closest thing quant finance has to a pre-registration theorist. His work does not use the term "pre-registration" but describes the same underlying epistemological problem from a statistical correction angle:

"The Deflated Sharpe Ratio" (Bailey & López de Prado, 2014)

- Published in *Journal of Portfolio Management* 40(5), pp. 94–107.
- Corrects the Sharpe ratio for: (a) number of trials/configurations tested, (b) non-normal return distributions (skewness, kurtosis), (c) sample length.
- Core insight: if you run N backtests and report only the best, the reported Sharpe is inflated by the selection process. DSR deflates it to the expected performance of an unselected test.
- hermes' pre-registration-v1 §8.1 uses DSR as its primary metric — directly implementing this paper's discipline.
- **Source:** [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551), [davidhbailey.com](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)

"Pseudo-Mathematics and Financial Charlatanism" (Bailey, Borwein, López de Prado, Zhu, 2014)

- Published in *Notices of the American Mathematical Society*.
- Demonstrates that high backtest performance is trivially achievable by searching enough configurations, and proposes CSCV (combinatorially symmetric cross-validation) to estimate the Probability of Backtest Overfitting (PBO).
- **Source:** [SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659)

"The 10 Reasons Most Machine Learning Funds Fail" (López de Prado, 2018)

- Published in *Journal of Portfolio Management* 44(6), pp. 120–133.
- Identifies: excessive in-sample optimization, curve fitting, multiple testing without correction, inadequate OOS validation, data contamination between IS/OOS splits, selection bias (post-hoc strategy selection), and failure to stress-test across regimes.
- Does NOT prescribe pre-registration explicitly — prescribes structural methodology (purged CV, walk-forward, DSR) as the cure.
- **Source:** [GARP whitepaper](https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf), [SSRN 3104816](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3104816)

"Advances in Financial Machine Learning" (2018, Wiley)

- Books introduces Purged k-fold Cross-Validation: removes training observations whose labels overlap with test labels. Also introduces Embargo (blocking data near test periods to prevent leakage).
- Combinatorial Purged Cross-Validation (CPCV) generates multiple train/test path combinations to overcome overfitting in standard k-fold.
- Central thesis: *in finance, the hardest problem is not prediction — it is validation.*
- hermes' pre-registration-v1 §8.3 uses Purged Combinatorial CV (5-fold, 5-day purge + 3-day embargo) — directly from this book.
- **Source:** [ETH library TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf), [Wikipedia: Purged cross-validation](https://en.wikipedia.org/wiki/Purged_cross-validation)

"A Practical Solution to the Multiple-Testing Crisis in Financial Research" (López de Prado, 2018)

- Proposes **post-hoc transparency as a substitute for pre-registration**: requires researchers to disclose all tests conducted, including discarded intermediate results. Combined with DSR adjustment, this estimates the probability that the reported best outcome is a false discovery.
- This is explicitly a *post-hoc* corrective mechanism, not a *pre-hoc* commitment. It is the closest López de Prado comes to pre-registration but stops short.
- **Source:** [AQR article](https://www.aqr.com/Insights/Research/Journal-Article/A-Data-Science-Solution-to-the-Multiple-Testing-Crisis-in-Financial-Research), [SSRN 3177057](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3177057), [PDF at lncohn.com](https://lncohn.com/finance/Prado-APracticalSolutionToMultiTestingCrisis.pdf)

López de Prado's overall prescription vs hermes:

| Aspect | López de Prado's prescription | hermes pre-registration-v1 |
| -------- | ------------------------------- | --------------------------- |
| Hypothesis pre-specification | Not required — corrected post-hoc via DSR | Required — frozen before any data access |
| Test count tracking | Disclose all N trials for DSR calculation | Lab notebook logs every trial (§12) |
| Data split | Purged CV, walk-forward | Purged CV + hard code wall on OOS |
| Kill criteria | No formal mechanism | Pre-specified, immutable (§10) |
| Multiple testing correction | DSR-based penalty | Bonferroni if secondary metrics added post-hoc (§8.4) |
| Document binding | None prescribed | SHA256 hash + git commit (§16) |

hermes uses López de Prado's statistical machinery **inside** a pre-registration wrapper he did not design.

---

### 1.5 Harvey, Liu & Zhu — "Factor Zoo" Multiple Testing Crisis

Confidence: H

Harvey, Liu & Zhu (2016) — "...and the Cross-Section of Expected Returns" — published in *Review of Financial Studies* 29(1), pp. 5–68.

- Catalogs ~400 factors published in top academic finance journals.
- Argues that with this volume of tested factors, conventional t-statistic thresholds (t > 2.0) cannot distinguish genuine predictors from false discoveries.
- Recommends that new factors must clear t > 3.0 to be considered credible, given the multiple-testing burden accumulated by prior research.
- "Most claimed research findings in financial economics are likely false."

This paper is the academic finance community's equivalent of psychology's replication crisis (2011–2015). It did not lead to pre-registration mandates in finance journals — it led to calls for higher statistical thresholds.

"False (and Missed) Discoveries in Financial Economics" (Harvey & Liu, 2020)

- Published in *Journal of Finance* 75(5).
- Follow-on paper: extends the multiple testing framework to consider both false positives (factors that don't exist) and false negatives (real factors missed because thresholds are too strict).
- **Source:** [Journal of Finance](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12951), [Harvey's website PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P143_False_and_missed.pdf)

---

### 1.6 Academic Pre-Registration in Finance: State of the Art

Confidence: M

Pre-registration is standard in medical trials (ClinicalTrials.gov, 1997 mandate) and well-established in RCT economics (AEA RCT Registry, launched 2013; OSF Registry, launched 2012). Finance is behind:

- The **AEA RCT Registry** grew from 7 pre-registered studies/year to 179/year over its first decade. These are primarily development economics RCTs, not quantitative finance strategies.
- The **Journal of Financial Economics**, *Review of Financial Studies*, and *Journal of Finance* have not adopted mandatory pre-registration. RFS now requires code/data replication for accepted papers. JFE acknowledges pre-registered work in its submission guidelines.
- **Registered Reports** (peer review of protocol before data collection, guaranteed publication conditional on execution) have been adopted by the Journal of Economic Science Association and Journal of Development Economics — not by the main finance journals.
- There are **zero known OSF-registered pre-analysis plans** for quantitative trading strategies (as of 2026). The OSF is used for psychology, medicine, economics of human subjects experiments — not algorithmic finance.

**Source:** [RFS Author Instructions](https://academic.oup.com/rfs/pages/Instructions_To_Authors), [AEA Registry overview](https://data.poverty-action.org/data-quality/aea-registry.html), [Pre-registration vs Registered Reports](https://www.aje.com/arc/pre-registration-vs-registered-reports), [OSF Preregistration guide](https://osf.io/6g5zv)

**Why finance lags medicine:** Medical trials have human subjects (ethical obligation to pre-register), regulatory mandate (FDA), and institutional IRB infrastructure. Quantitative trading has none of these external forces. The incentive is the opposite: funds want secrecy, not transparency.

---

### 1.7 Hypothesis-First Practitioner Movement (Nascent)

Confidence: M

A small practitioner blogosphere has begun advocating hypothesis-first quant research as an anti-overfitting practice:

- **Unbiased Alpha blog (2023–2024)** explicitly names the practice "pre-registration for traders" and lists what must be written down before backtesting: signal formula, holding period, performance metric, pass threshold, instrument universe. Quote: "This is your pre-registration. Even if you're not submitting it to an academic journal, writing it down forces you to commit."
- **Interpretable Hypothesis-Driven Trading (2024 arXiv paper, Deep et al.)** — implements a pre-committed analysis plan with strict information set discipline and walk-forward validation across multiple market regimes. Items in the plan are fixed before any out-of-sample results are examined; deviations must be explicitly reported and justified.

Neither of these frameworks includes: (a) immutable hash-binding of the document, (b) pre-specified kill criteria with post-kill protocol, (c) role separation (theorist / experimentalist / integrity checker), or (d) data manifest fingerprinting.

**Source:** [Unbiased Alpha — Hypothesis First](https://unbiased-alpha.com/how-to-avoid-backtest-overfitting-hypothesis-driven-strategy-discovery), [arXiv 2512.12924](https://arxiv.org/pdf/2512.12924)

---

### 1.8 Prediction Markets (Metaculus, Manifold, Kalshi)

Confidence: L

Prediction markets enforce pre-commitment in the sense that you must state your probability estimate before the event resolves. However:

- Questions are posed by platform moderators, not the forecaster — the forecaster does not pre-register their own hypothesis.
- Scoring is calibration-based (Brier score, log score), not strategy performance-based.
- No equivalent to: IS/OOS data split, kill criteria, multiple-testing adjustment, or experiment lifecycle.
- Metaculus's weighted aggregation (gives more weight to historically calibrated forecasters) has structural similarity to Numerai's tournament meta-model, but neither is pre-registration.

**Tangential relevance to hermes:** Prediction markets show that pre-commitment to a probabilistic claim (with financial incentive) improves calibration. This supports the theoretical motivation for hermes' pre-registration but is not a direct prior art.

**Source:** [Metaculus LongtermWiki entry](https://ea-crux-project.vercel.app/knowledge-base/organizations/metaculus/), [26 Forecasting Platforms course notes](https://corporate.jasoncollins.blog/forecasting-platforms)

---

## 2. What hermes Borrowed From Where

| hermes v1 Element | Origin |
| ------------------- | -------- |
| Pre-specified hypothesis (H1/H0) | Clinical trial model (IRB protocol) / Physics pre-registration |
| Deflated Sharpe Ratio as primary metric | Bailey & López de Prado (2014), directly cited in §8.1 |
| Purged Combinatorial CV (5-fold, 5-day purge, 3-day embargo) | López de Prado AFML (2018), directly cited in §8.3 |
| Walk-forward expanding window | Standard quant methodology, formalized by López de Prado |
| Lab notebook as anti-p-hacking mechanism | Open Science Framework practices / clinical trial lab journals |
| Bonferroni correction for post-hoc secondary metrics | Standard multiple-testing discipline (§8.4) |
| SHA256 hash binding of the document | Numerai's blockchain commitment → adapted to git hash |
| Dataset fingerprints (data-manifest.json) | Open science data provenance practices |
| Kill criteria (pre-specified, immutable) | Clinical trial stopping rules (DSMB) |
| Post-kill protocol (7-day post-mortem, new pre-registration) | Physics experiment protocol discipline |
| Three-role model (theorist / experimentalist / checker) | Physics experiment separation of concerns; clinical IRB independence |
| Correlation lock (SA-9 RO-01) | Portfolio construction literature (Markowitz, López de Prado AFML ch. 16) |
| Bootstrap CI on OOS Sharpe (10,000 resamples, block 30 days) | Statistical bootstrap literature; block length from financial time-series autocorrelation |
| Chandelier + Donchian + ADX strategy | Clenow / Faber trend-following literature |

---

## 3. What Is Novel About hermes' Approach

Confidence: H on novelty claims; these are absence-of-evidence claims, not evidence-of-absence.

The following elements appear to have no prior art in quantitative finance:

1. **Full clinical-trial lifecycle applied to a single strategy** — The complete IRB analogy: pre-specification before data access → shadow phase → OOS certification → live approval → CERTIFIED status. This lifecycle exists in pharmaceutical development; no quant fund has published an equivalent framework for a trading strategy.

2. **Code-enforced OOS wall** — "Engine physically refuses to read the OOS window outside certification mode" (§4). QuantConnect and Numerai enforce temporal separation at the platform level. hermes enforces it at the engine code level within the same codebase — making cheating require an active code change (auditable).

3. **Pre-specified kill criteria as part of the pre-registration** — López de Prado prescribes DSR but does not pre-specify kill criteria. Numerai's staking enforces consequences but does not pre-specify when to declare the model "dead." hermes §10 defines exact thresholds for halt before any live trading begins.

4. **Post-kill protocol requiring a new pre-registration for any variant** — "Kill = strategy DEAD for this pre-registration. New pre-registration required to attempt a variant." No equivalent found in any public quant framework.

5. **Three-role separation with integrity checker** — Theorist / Experimentalist / Integrity Checker roles with explicitly different authorities. Analogous to IRB independent review board but applied to a trading experiment. Not documented in any quant methodology.

6. **SHA256 of the pre-registration + data manifest as linked immutable record** — Numerai uses blockchain timestamps on prediction submissions. hermes applies this to the experiment *design* document (the pre-registration itself), not the predictions. This is closer to clinical trial protocol registration with ClinicalTrials.gov.

7. **Crypto-specific regime coverage specification** — §4 explicitly lists which market regimes the train/validation window covers (COVID crash, Terra/Luna, FTX, SVB, Aug 5 2024 volatility). This is a pre-registered claim about distributional coverage — novel in crypto context.

**What hermes is unknowingly duplicating (low confidence):** Some internal quantitative hedge funds (Two Sigma, D.E. Shaw, AQR) likely have internal research discipline protocols that include pre-specification of hypotheses and kill criteria. These are not published. It is plausible hermes has independently reinvented something that exists at these firms but has never been made public. The practitioner blogosphere's "hypothesis-first" movement is the closest public analogue.

---

## 4. Lessons From Public Failures That Reinforce Pre-Registration

### 4.1 LTCM 1998 — The Correlation Regime Failure

Confidence: H

Long-Term Capital Management managed by Nobel laureates (Merton, Scholes) failed in September 1998 after the Russian financial crisis.

**The pre-registration failure mode:** LTCM's VaR model was calibrated on a dataset that excluded both the 1987 crash and the 1994 bond crisis (the two largest disruptions before their strategy's inception). The model pre-supposed correlation between positions of ~0.30. During the 1998 crisis, realized correlation jumped to ~0.70, turning nominally diversified bets into one concentrated trade. The fund lost 90% of capital in weeks.

What pre-registration would have required:

- A pre-specified regime-coverage statement (analogous to hermes §4's regime coverage table) — would have forced acknowledgment that 1987 and 1994 were excluded.
- Pre-specified correlation assumptions with stress-test scenarios — equivalent to hermes §5.5's correlation lock.
- A kill criterion based on correlation spikes — equivalent to hermes §10's SA-9 P0 rules.

**Lesson for hermes:** The correlation lock at 0.80 (§5.5) and the SA-9 depeg overlay are directly LTCM-lesson mechanisms, even if not credited. Pre-registering these constraints before seeing 2022 crypto data is structurally identical to what LTCM failed to do with 1987/1994 data.

**Source:** [Six lessons from LTCM collapse](https://www.timeline.co/blog/six-lessons-from-the-collapse-of-ltcm), [Berkeley LTCM lessons paper](https://eml.berkeley.edu/~webfac/craine/e137_f03/137lessons.pdf)

---

### 4.2 Ronin Capital 2020 — Model Didn't Include COVID Volatility Regime

Confidence: M

Ronin Capital (Chicago's largest prop trading firm) collapsed in March 2020 when VIX spiked during COVID. Their volatility-selling strategy (short VIX futures) was calibrated on pre-COVID volatility regimes. VIX more than doubled in days; their capital dropped below CME minimum requirements.

**The pre-registration failure mode:** No public post-mortem, but the structural failure is clear — a strategy calibrated in a low-volatility regime with no pre-specified kill criterion for regime change. Ronin's connected entity Parplus Partners caused ABN Amro $200M in losses through the same mechanism.

**Lesson for hermes:** hermes §4 explicitly covers "2020 Q1: COVID crash + recovery" in the train window. The shadow certification gate (90-day live shadow: sim-vs-live drift < 30 bps p50) would catch systematic execution failure before live capital is committed.

**Source:** [Crain's Chicago Business — Ronin fell](https://www.chicagobusiness.com/finance-banking/how-ronin-capital-fell-grace-covid-spread/), [CNBC Ronin CME capital requirements](https://www.cnbc.com/2020/03/20/clearing-firm-ronin-capital-unable-to-meet-capital-requirements-at-cme-sources.html)

---

### 4.3 Three Arrows Capital / Alameda Research 2022 — Crypto Regime Collapse

Confidence: M

3AC and Alameda are not strictly model-overfitting failures — they were leverage and fraud failures respectively. However, they illustrate the regime-change risk specific to crypto:

- **3AC** invested heavily in LUNA/UST assuming the algorithmic stablecoin peg was robust. LUNA collapsed to near zero in May 2022. The founders admitted they "failed to realize LUNA was capable of falling to near zero in days." This is a pre-specification failure: no pre-registered kill criterion for a stablecoin depeg scenario.
- **Alameda** was primarily a balance-sheet and conflict-of-interest failure (FTX fraud), not a strategy-overfitting failure.
- The 2022 crypto quant fund landscape broadly suffered from models trained on 2020–2021 bull-market data failing to generalize to the bear market and regime change — exactly the overfitting failure mode López de Prado's work predicts.

**Lesson for hermes:** SA-9's depeg overlay (one of hermes' kill triggers) directly addresses the 3AC-LUNA failure mode. Pre-registering this before any live capital is the discipline 3AC lacked.

**Source:** [3AC founders CoinDesk](https://www.coindesk.com/business/2022/07/22/three-arrows-capital-founders-says-terra-gbtc-trades-led-to-fund-blowup-report), [3AC Wikipedia](https://en.wikipedia.org/wiki/Three_Arrows_Capital)

---

### 4.4 AQR Value Drawdown 2018–2020 — Factor Timing Failure

Confidence: M

AQR Capital suffered significant value-factor drawdowns from 2018–2020. Asness published public explanations but no "post-mortem" in the fund-failure sense — AQR survived.

**The research discipline angle:** AQR's internal research culture is described as evidence-first (Asness credits Fama), stress-testing statistical assumptions in peer review. Yet Asness acknowledged that backtests of value factor returns were optimistic about live performance during extended drawdowns. This illustrates that even top-tier research discipline does not eliminate IS/OOS gap — it only reduces it.

Asness's taxonomy of profitable backtests: (1) data torture (noise), (2) risk compensation (genuine), (3) behavioral bias (genuine). Machine learning makes data torture easier, creating more Type-I errors even with cross-validation.

**Source:** [Three types of backtests — Asness](https://www.priceactionlab.com/Blog/2023/03/three-backtests-assness/), [AQR: Factor Timing is Hard](https://www.aqr.com/Insights/Perspectives/Factor-Timing-is-Hard)

---

### 4.5 The Factor Zoo Replication Crisis (2016–ongoing)

Confidence: H

Harvey, Liu & Zhu's 2016 finding that ~400 published factors may largely be false discoveries constitutes the largest structural argument for pre-registration in finance that exists. This is not a single fund failure but a systematic indictment of an entire research methodology.

**Specific numbers:** The factor count in academic finance grew from under 100 in 2003 to ~400 by 2016. Given the multiple-testing burden, Harvey et al. estimate that most of these "discovered" factors would fail to replicate in truly OOS data.

**Lesson for hermes:** The DSR-based primary metric (§8.1) and trial count in the lab notebook (§12) are direct responses to this crisis. Every backtest run during train+val counts as a trial; total logged and used in DSR deflation. This is exactly what Harvey et al. prescribe as the minimum credibility requirement.

**Source:** [Harvey et al. 2016 — RFS](https://academic.oup.com/rfs/article/29/1/5/1843824), [Harvey & Liu 2020 — Journal of Finance](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12951)

---

## 5. What Is Missing / Not Covered by Prior Art

The following hermes v1 elements have no known prior art in any public quant finance or academic pre-registration framework:

| Gap | Description |
| ----- | ------------- |
| **Experiment lifecycle states** (UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED → OOS_CERTIFIED → LIVE_APPROVED → CERTIFIED) | No public quant framework defines a formal state machine for strategy certification. FDA drug approval is the closest analogue (IND → Phase I → Phase II → Phase III → NDA). |
| **Shadow phase as intermediate gate** | Clinical trials have Phase II before Phase III; quant frameworks jump from backtest to live. hermes' 90-day shadow phase (sim-vs-live drift < 30 bps p50) is novel. |
| **Data manifest with SHA256 fingerprints** | Academic reproducibility frameworks (e.g., RFS code requirement) don't extend to cryptographic fingerprinting of the specific data used. |
| **Deviation protocol distinguishing invalidating vs non-invalidating changes** | No public quant framework defines exactly what constitutes an experiment-invalidating deviation vs a permitted correction. |
| **Pre-registered slippage model** (5 bps entry, 15 bps exit stop-hunt) | Standard backtesting practice uses some slippage, but pre-registering the exact model before backtesting (not tuned to make results look better) has no documented prior art. |

---

## 6. References

All claims above are sourced. URL confidence ratings: H = primary source; M = secondary source or partially paywalled; L = tertiary or inferred.

| Reference | URL | Confidence |
| ----------- | ----- | ------------ |
| Bailey & López de Prado, "Deflated Sharpe Ratio" (2014) | [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) / [PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | H |
| Bailey et al., "Pseudo-Mathematics and Financial Charlatanism" (2014) | [SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659) | H |
| Bailey et al., "Probability of Backtest Overfitting" (2015) | [SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | H |
| Harvey, Liu & Zhu, "...and the Cross-Section of Expected Returns" (2016) | [RFS](https://academic.oup.com/rfs/article/29/1/5/1843824) / [SSRN 2249314](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314) | H |
| Harvey & Liu, "False (and Missed) Discoveries in Financial Economics" (2020) | [Journal of Finance](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12951) / [PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P143_False_and_missed.pdf) | H |
| López de Prado, "10 Reasons Most ML Funds Fail" (2018) | [GARP PDF](https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf) / [SSRN 3104816](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3104816) | H |
| López de Prado, "Advances in Financial Machine Learning" (2018) | [ETH TOC](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf) / [Purged CV Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation) | H |
| López de Prado, "A Practical Solution to the Multiple-Testing Crisis" (2018) | [AQR](https://www.aqr.com/Insights/Research/Journal-Article/A-Data-Science-Solution-to-the-Multiple-Testing-Crisis-in-Financial-Research) / [PDF](https://lncohn.com/finance/Prado-APracticalSolutionToMultiTestingCrisis.pdf) | H |
| QuantConnect Alpha Streams submission rules | [9 Ways to Get Your Alpha Rejected](https://www.quantconnect.com/forum/discussion/6737/9-ways-to-get-your-alpha-rejected/) | H |
| QuantConnect Alpha Streams — OOS verification | [BusinessWire 2020](https://www.businesswire.com/news/home/20200827005181/en/QuantConnect-Increases-Access-to-Alpha-Streams-Market) | H |
| Numerai staking mechanics | [Numerai Staking docs](https://docs.numer.ai/numerai-tournament/staking) | H |
| Numerai submissions / OOS structure | [Numerai Submissions](https://docs.numer.ai/numerai-tournament/submissions) | H |
| Numerai blockchain commitment (Erasure) | [Numerai Gemini overview](https://www.gemini.com/cryptopedia/numerai-tournaments) | M |
| WorldQuant IQC Guidelines | [IQC Guidelines](https://www.worldquant.com/brain/iqc-guidelines/) | H |
| AQR — Asness on three types of backtests | [Price Action Lab](https://www.priceactionlab.com/Blog/2023/03/three-backtests-assness/) | M |
| AQR — Factor Timing is Hard | [AQR Perspectives](https://www.aqr.com/Insights/Perspectives/Factor-Timing-is-Hard) | H |
| LTCM post-mortem lessons | [Berkeley PDF](https://eml.berkeley.edu/~webfac/craine/e137_f03/137lessons.pdf) / [Timeline blog](https://www.timeline.co/blog/six-lessons-from-the-collapse-of-ltcm) | M |
| Ronin Capital collapse | [Crain's Chicago](https://www.chicagobusiness.com/finance-banking/how-ronin-capital-fell-grace-covid-spread/) / [CNBC](https://www.cnbc.com/2020/03/20/clearing-firm-ronin-capital-unable-to-meet-capital-requirements-at-cme-sources.html) | M |
| 3AC collapse | [CoinDesk](https://www.coindesk.com/business/2022/07/22/three-arrows-capital-founders-says-terra-gbtc-trades-led-to-fund-blowup-report) | M |
| Crypto quant overfitting — Risk.net | [Risk.net](https://www.risk.net/investing/quant-investing/7873646/quant-funds-tackle-chronic-overfitting-in-crypto-strategies) | M (paywalled) |
| Hypothesis-first trading practice | [Unbiased Alpha](https://unbiased-alpha.com/how-to-avoid-backtest-overfitting-hypothesis-driven-strategy-discovery) | M |
| Pre-committed analysis plan paper | [arXiv 2512.12924](https://arxiv.org/pdf/2512.12924) | M |
| AEA RCT Registry | [Data Poverty Action](https://data.poverty-action.org/data-quality/aea-registry.html) | H |
| OSF Preregistration | [OSF](https://osf.io/6g5zv) / [COS](https://www.cos.io/initiatives/prereg) | H |
| RFS author instructions (code requirement) | [Oxford Academic](https://academic.oup.com/rfs/pages/Instructions_To_Authors) | H |

---

End of prior art memo.
