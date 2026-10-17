# CI Runners and the Merge Queue

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole reviewer)
**Applies to**: whoever operates the self-hosted runners on Workbench, and anyone
debugging why a PR has not merged.
**Description**: Where CI jobs run, how they are routed, and how the merge queue batches, gates and merges approved PRs.

The runners live in Docker on Workbench. Their stack is in
`scripts/dev/gh-runner/`. The merge queue is
`.github/workflows/merge-queue.yml`, and its decisions are in
`tools/merge/decisions.cjs`. This guide shows how the pieces fit. The
files are the source of truth.

## Runners and routing

```mermaid
flowchart LR
    subgraph Workbench["Workbench (Docker Desktop VM)"]
        CI["runner x N<br/>label: hermes"]
        OPS["ops-runner x 1<br/>label: hermes-ops"]
        REV["review-runner x 2<br/>label: hermes-review"]
        DEP["dependabot-runner x 1<br/>label: dependabot"]
        DIND["dependabot-dind<br/>(private Docker daemon)"]
        DEP --> DIND
    end
    PRCI["ci.yml, scheduled.yml"] -->|RUNNER_LABEL| CI
    Q["merge-queue.yml, cd.yml"] -->|OPS_RUNNER_LABEL,<br/>falls back to RUNNER_LABEL| OPS
    AIR["ai-review.yml"] -->|REVIEW_RUNNER_LABEL,<br/>falls back to RUNNER_LABEL| REV
    DB["Dependabot Updates"] -->|label dependabot| DEP
```

- **CI runners** (`runner`, label `hermes`) run PR CI and the scheduled
  checks. Each PR's whole suite is one `CI success` job, so one runner
  serves one PR from start to finish. The pool size and limits are in
  `compose.yml`; sizing them to the VM is #523.
- **The ops runner** (`hermes-ops`) serves the merge queue and CD, so a
  merge or a deploy never waits behind PR CI. Workflows pick it through
  the repo variable `OPS_RUNNER_LABEL`. Until that is set, they fall back
  to the CI pool.
- **The review runners** (`hermes-review`) run only the AI review. A
  review takes about 7 minutes, mostly waiting on the model, so two small
  runners (1 CPU, 1.5 GB) keep it from holding a CI runner while PR
  tests queue (#631). `ai-review.yml` picks them through the repo
  variable `REVIEW_RUNNER_LABEL` and falls back to the CI pool until it
  is set.
- **The Dependabot runner** has its own Docker-in-Docker sidecar on a
  private network. GitHub needs a runner that can run Docker for
  Dependabot updates, and this keeps the host's Docker socket out of
  reach.
- Every runner is ephemeral. It takes one job, exits and re-registers,
  so one runner showing offline between jobs is normal.
  `hermes runners` (#516) reports each group's health.
- **No job ever runs on a GitHub-hosted runner.** The deploy secrets and the
  write token must stay on server-01 (Rick, 17 Oct 2026; issue #754). A job
  whose runner variable is unset does not fall back to a hosted image: its
  expression ends in the fixed label set `["self-hosted","linux","hermes"]`,
  so it queues for a server-01 runner. `scripts/dev/runs-on-check.py`, part of
  the `workflows` check in PR CI and `pr-preflight.sh`, fails any `runs-on`
  that names a hosted image or lacks that self-hosted ending (#1069).

Runner commands, all run from `scripts/dev/gh-runner` on Workbench:

| Task | Command |
| --- | --- |
| Start or resize the CI pool | `./up.sh <N>` |
| Start the ops and Dependabot runners | `docker compose up -d --no-recreate ops-runner dependabot-dind dependabot-runner` |
| Start the AI review runners | `docker compose up -d --no-recreate review-runner` |
| Tail a runner | `docker compose logs -f runner` |
| Stop everything | `./down.sh` |

## The merge queue

```mermaid
flowchart TD
    T["Trigger: approval, CI finished,<br/>push to main, hourly, dispatch"] --> S
    S["Select: approved, green, non-draft PRs,<br/>oldest approval first"] --> B
    B["Build one tree: main + PR1 + PR2 + ..."] --> G{"Gates on the tree"}
    G -- green --> M["Squash-merge each PR in order,<br/>pinned to its tested head"]
    G -- red --> X["Bisect: gate halves until<br/>the breaking PR is found"]
    X --> L["Label merge-queue:gates-failed,<br/>red status, one comment"]
    X --> M
    M --> C{"main's tree equals<br/>the gated tree?"}
    C -- yes --> D["Skip CI on main"]
    C -- no --> R["Dispatch CI on main"]
```

- **Select** skips a PR it can't merge itself, such as a workflow change
  without the App's Workflows permission, and labels it
  `merge-queue:hand-merge`. It also skips a head that already failed the
  gates, until a new push or the label's removal.
- **Gates** run once per batch tree from `scripts/dev/merge-gates.sh`:
  markdownlint, gitleaks, ruff, mypy, lint-imports, and unit plus
  integration tests. The queue copies its own tooling from main's tip
  before merging anything in, so a PR can't change the gates that judge
  it.
- **Merging** never pushes to a PR branch and never updates one. The
  ruleset doesn't require up-to-date branches, because the queue proves
  the merged tree instead.

## When a PR does not merge

| Symptom | Where to look |
| --- | --- |
| Approved, but the queue ignores it | The Select log: not approved, draft, conflicts, or `blocked` (a required check is red or pending) |
| `merge-queue:hand-merge` label | The PR comment says why; merge it by hand or fix the cause and remove the label |
| `merge-queue:gates-failed` label | The PR comment names the failing tests; push a fix |
| "Merges cleanly with main" is red | Merge main into the branch and resolve the conflict |
| CI slow or stuck | `python3 scripts/dev/ci-timing.py` (#521) and `python3 scripts/dev/ci-flakes.py` (#565) |
