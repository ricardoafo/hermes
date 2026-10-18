# Verdict records

A verdict record says whether one candidate passed one stage of a research
batch. The results job writes it after a batch run, and the console's Lab
screen (issue #1056) reads it. It holds PASS, FAIL or NA and nothing else
about the result: no metric value ever lands in it
([ADR-012](../architecture/adr-012-continuous-ai-evaluation.md) D-06,
[ADR-026](../architecture/adr-026-research-batch-and-signed-champion-rule.md) §6).

## Path

`docs/experiments/<experiment>/verdicts/<batch-id>/<strategy-id>.<stage>.json`

A candidate has one file per stage, `train` and `validation`. A candidate that
did not go on to Validation has no `validation` file.

## Fields

Schema name: `hermes.verdict/1`. A record with any other field is refused.

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema` | string | Always `hermes.verdict/1` |
| `batch_id` | string | Plain name of the batch |
| `strategy_id` | string | Plain name of the candidate |
| `idea_family` | string | Plain name of the candidate's family |
| `stage` | string | `train` or `validation` |
| `run_id` | whole number or null | The workflow run that produced it, filled in by the results job |
| `commit` | string or null | The commit the run used, filled in by the results job |
| `window` | object or null | `start` and `end`, both `YYYY-MM-DD`, the dates the stage covered; null when the candidate had no evidence (its only check is `missing_evidence`, FAIL) |
| `trial_lines` | whole number or null | How many trial log lines the stage added |
| `checks` | list | One `{check, status}` entry per certify or funnel check |
| `passed` | boolean | Whether the candidate passed the stage |
| `goes_on` | boolean or null | Whether it goes on to the next stage; null when not decided |
| `champion` | boolean or null | Whether the champion rule picked it; null when not decided |

`check` is a lowercase name of letters and underscores. `status` is one of
`PASS`, `FAIL` or `NA` (not applicable).

## What is refused

The job validates every record before it writes anything. It stops, and the
tree stays untouched, when a record has an unknown field, a number anywhere but
`run_id` and `trial_lines`, a non-boolean `passed`, `goes_on` or `champion`, a
bad date (a null window is allowed), a check that is not `{check, status}`, or a name with anything but lowercase
letters, digits and dashes (`batch_id`, `strategy_id`, `idea_family`).

The champion's id, or `none`, goes in the results PR's notes, not in a
separate file.
