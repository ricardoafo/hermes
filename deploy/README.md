# deploy/ — VPS Provisioning + Systemd Units

**Type**: Deploy assets (living scaffolding)
**Owners**: Ricardo Afonso
**Description**: VPS bootstrap script, systemd units, and reference material for the CD pipeline defined in `.github/workflows/cd.yml`.

## Status

Skeleton — every asset here is a placeholder that pins the SHAPE of
the deploy target while Phase 0 fills in the payload. See ADR-003 §09
(`docs/architecture/adr-003-solution-architecture/09-deployment.md`)
for the target design.

## End-to-end flow

```mermaid
flowchart TB
    subgraph PR["PR lifecycle (feature branch)"]
        P[Open PR]
        P --> CI["ci.yml runs<br/>15 jobs<br/>(changeset → quality/unit/... → ci-success)"]
        CI --> A{approved?}
        A -- no --> P
    end

    A -- yes --> Q["merge-queue.yml<br/>revalidate-then-merge queue<br/>builds merge tree locally"]
    Q --> M["Squash-merge to main<br/>(delete_branch_on_merge=true)"]

    M --> WR["workflow_run: CI on main"]
    WR --> CIMAIN["ci.yml runs on main SHA<br/>(same checks, re-validates squash)"]
    CIMAIN --> PMAIN["push:main event fires cd.yml"]

    subgraph CD["cd.yml — 5 jobs"]
        B["build<br/>uv build → wheel artifact"]
        D["deploy<br/>scp + ssh + uv pip install<br/>+ systemctl restart hermes-*.service<br/>environment: prod"]
        H["health-check<br/>curl healthchecks.io URL<br/>poll ×24 @ 5s"]
        S["smoke<br/>pytest -m smoke against deployed instance"]
        C["confirm<br/>aggregator: all above green?"]
        B --> D --> H
        D --> S
        H --> C
        S --> C
    end

    PMAIN --> B

    C --> VPS["VPS hermes-01<br/>(Tailscale-only SSH)"]
    VPS --> SD["systemd units<br/>hermes-health.timer (placeholder)<br/>hermes-rebalancer.timer (Phase 0)<br/>hermes-swing.service (Phase 1)<br/>hermes-backup.timer (Phase 2)"]

    SD -.->|ping every 5min| HC["healthchecks.io<br/>dead-man's-switch"]
    HC -.->|silent → alert| TG["Telegram bot"]

    style B fill:#fff3cd
    style D fill:#fff3cd
    style H fill:#fff3cd
    style S fill:#fff3cd
    style C fill:#d4edda
```

Yellow = placeholder body (fill in Phase 0). Green = real logic today.

## Trigger order

1. PR opens → CI runs (all 15 jobs, gated on changeset)
2. Human approves → `merge-queue.yml` picks it up
3. Queue builds merge tree locally, runs tree gates on it, calls `pulls.merge`
4. Squash lands on `main` → GH fires `push:main` + `workflow_run:CI:completed`
5. `cd.yml` triggers on `push:main` — builds wheel, deploys, verifies
6. `confirm` aggregator marks the deployment complete
7. Systemd services on VPS pick up the new wheel on their next unit start

Cross-references:

- Queue: `.github/workflows/merge-queue.yml`, `tools/merge/decisions.cjs`, `docs/decisions/2026-08-21-revalidate-then-merge.md`
- CI: `.github/workflows/ci.yml`
- CD: `.github/workflows/cd.yml` (this dir)
- Runtime: `docs/architecture/adr-003-solution-architecture/09-deployment.md`

## Contents

| File                            | Purpose                                                                                                | Status                                     |
| ------------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| `bootstrap-vps.sh`              | Idempotent VPS provisioning (user, dirs, uv, systemd-creds, Tailscale)                                 | skeleton — safe to re-run when filled in   |
| `systemd/hermes-health.service` | Minimal placeholder service — exists so the deploy pipeline has something to restart before Phase 0    | placeholder                                |
| `systemd/hermes-health.timer`   | Fires the placeholder every 5 min so we can validate the health-check job end-to-end                   | placeholder                                |
| `systemd/hermes.service`        | Template unit for the main hermes process (reads `/etc/hermes.env`, runs `python -m hermes serve`)     | template — copy-and-install, not auto-set  |
| `systemd/hermes.env.example`    | Shape of `/etc/hermes.env` — the EnvironmentFile prod systemd loads. Documentation only, no secrets    | template — never installed automatically   |

## First-time VPS setup

Run once as root on `hermes-01` (via `ssh -i ~/.ssh/hermes_vps_ed25519 root@hermes-01`):

```bash
curl -fsSL https://raw.githubusercontent.com/ricardoafo/hermes/main/deploy/bootstrap-vps.sh | sudo bash
```

Or clone the repo and run locally:

```bash
git clone https://github.com/ricardoafo/hermes.git
cd hermes
sudo bash deploy/bootstrap-vps.sh
```

The script is idempotent — re-running is safe and only reconciles
missing state.

## Post-bootstrap: install systemd units

```bash
sudo cp deploy/systemd/hermes-health.service /etc/systemd/system/
sudo cp deploy/systemd/hermes-health.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-health.timer
```

Verify:

```bash
systemctl status hermes-health.timer
journalctl -u hermes-health.service --since "5 minutes ago"
```

## Post-bootstrap: install the main hermes service + env file

Prod credentials live in `/etc/hermes.env` (read by systemd via
`EnvironmentFile=`) — NOT in a dotenv file inside `/opt/hermes`. The
file must be root-owned and 0600 so `hermes` (or any other user) cannot
read the secrets directly; systemd expands the env into the service
process at start time only. See
`docs/decisions/2026-08-23-credential-storage-layered-by-env.md` for
the three-tier rationale (dotenv for dev / GH Secrets for CI / systemd
EnvironmentFile for prod).

```bash
# 1. Install the env file from the committed example, then edit it in place.
sudo install -o root -g root -m 0600 \
    deploy/systemd/hermes.env.example /etc/hermes.env
sudo $EDITOR /etc/hermes.env    # populate real secrets

# 2. Verify permissions — MUST be 0600 root:root. Any other value is a leak.
stat -c '%a %U:%G' /etc/hermes.env   # expect: 600 root:root

# 3. Install the systemd unit.
sudo cp deploy/systemd/hermes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes.service
```

Reload after editing `/etc/hermes.env` (systemd caches the file into
each running unit — restart is required):

```bash
sudo $EDITOR /etc/hermes.env
sudo systemctl daemon-reload         # picks up EnvironmentFile changes
sudo systemctl restart hermes.service
journalctl -u hermes.service --since "1 minute ago"
```

Verify:

```bash
systemctl status hermes.service
journalctl -u hermes.service --since "5 minutes ago"
```

## GH Actions secrets required (populate on `prod` Environment)

The `cd.yml` workflow references these secrets. Provision them under
Repo Settings → Environments → `prod`:

| Secret                | Purpose                                                                    |
| --------------------- | -------------------------------------------------------------------------- |
| `VPS_SSH_KEY`         | Private SSH key for the deploy user (ed25519)                              |
| `VPS_SSH_HOST`        | Tailscale hostname (`hermes-01`) — port 22 is CLOSED to public internet    |
| `VPS_SSH_USER`        | Deploy user (`hermes-deploy` — different from `hermes` service user)       |
| `VPS_HEALTHCHECK_URL` | Healthchecks.io ping URL for post-deploy verification                      |

Environment branch restriction: `main` only — no fork-PR workflows can
resolve these.

## Related

- `.github/workflows/cd.yml` — CD pipeline
- `docs/architecture/adr-003-solution-architecture/09-deployment.md` — full deploy design
- `docs/guides/vps-operations.md` — VPS ops runbook
