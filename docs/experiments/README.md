# Experiments — pre-registered strategy experiments

Each experiment folder holds the documents that bind one strategy
experiment: the signed pre-registration, the success criteria and
statistics it is judged by, the trial log that counts every run, the lab
notebook, and the post-mortem when a trial ends. A signed document is never
edited; a change is a new version (see
[ADR-014](../architecture/adr-014-pre-registration-binds-real-capital.md)
and [ADR-021](../architecture/adr-021-strategy-lifecycle-ready-and-done.md)).

## Index

| Folder or file | What it holds |
| -------------- | ------------- |
| [swing-mvp1/](#swing-mvp1) | The Swing MVP1 experiment, listed below |
| [templates/hypothesis-card.md](templates/hypothesis-card.md) | Template for one research candidate's hypothesis card |
| [templates/research-batch.md](templates/research-batch.md) | Template for a research batch document that signs several cards together |
| [verdicts.md](verdicts.md) | Schema of the PASS/FAIL verdict records a batch run commits |

### swing-mvp1

| File | What it holds | Status |
| ---- | ------------- | ------ |
| [pre-registration-v0.md](swing-mvp1/pre-registration-v0.md) | First pre-registration draft | Draft |
| [pre-registration-v1.md](swing-mvp1/pre-registration-v1.md) | Pre-registration v1 | Signed |
| [pre-registration-v2.md](swing-mvp1/pre-registration-v2.md) | Pre-registration v2 | Signed |
| [success-criteria.md](swing-mvp1/success-criteria.md) | Success criteria and how progress is measured | Draft |
| [statistical-framework.md](swing-mvp1/statistical-framework.md) | Statistical framework the gates use | Draft |
| [integrity-checker-rules.md](swing-mvp1/integrity-checker-rules.md) | Rules the integrity checker enforces | Active |
| [data-ingest-plan.md](swing-mvp1/data-ingest-plan.md) | Plan for ingesting the market data | Draft |
| [regime-detection-notes.md](swing-mvp1/regime-detection-notes.md) | Research notes on regime detection, not a specification | Draft |
| [lab-notebook.md](swing-mvp1/lab-notebook.md) | Lab notebook of the experiment | Living |
| [post-mortem-template.md](swing-mvp1/post-mortem-template.md) | Trial termination report template | Template |
| [post-mortem-v2.md](swing-mvp1/post-mortem-v2.md) | Trial termination report for v2 | Draft |
| [REVIEW-BRIEF.md](swing-mvp1/REVIEW-BRIEF.md) | Overnight research brief on the paper-trading engine | Dated |
| [cards/](swing-mvp1/cards/README.md) | Hypothesis cards of the research candidates | Living |
| [data-manifest.json](swing-mvp1/data-manifest.json) | Pinned dataset manifest | Data |
| [fee-history.csv](swing-mvp1/fee-history.csv) | Exchange fee history the backtests charge | Data |
| [trial-log.jsonl](swing-mvp1/trial-log.jsonl) | Every trial run, counted in the DSR | Data |
