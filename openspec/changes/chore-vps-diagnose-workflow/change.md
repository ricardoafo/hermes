# Change: chore-vps-diagnose-workflow

**SDD change**: `chore-vps-diagnose-workflow`
**Date**: 2026-08-26
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#276)

Mirror of engram topic key `sdd/chore-vps-diagnose-workflow/change`.

## Summary

Adds a manual `workflow_dispatch`-only workflow that SSHes into the provisioned CX23 VPS using the existing `prod` Environment secrets (`VPS_SSH_HOST`, `VPS_SSH_USER`, `VPS_SSH_KEY`) and emits a structured 10-section snapshot of the VPS state — OS, Python + uv, `/opt/hermes` contents, systemd units, storage-box mount, disk + memory, firewall, fail2ban, GH Actions runner. Zero side effects: READ commands only. Serves two purposes: (1) unblocks #179 (CD-to-VPS body) by giving the operator + agent ground-truth on what's already provisioned vs missing; (2) validates the secret plumbing that the future deploy body will use.

## Scope

### In

- **NEW**: `.github/workflows/vps-diagnose.yml` (~150 LOC) — workflow_dispatch trigger, `environment: prod`, uses `webfactory/ssh-agent@v0.9.0` to load `VPS_SSH_KEY`, then a single SSH heredoc executing 10 read-only diagnostic sections.
- **NEW**: `changelog.d/wave-chore-vps-diagnose-workflow.md`.

### Out

- Any WRITE operations against the VPS (installs, systemctl restarts, git pulls, etc.). Those live in the CD workflow's deploy body — this diagnostic is strictly read-only.
- Scheduled or automated invocation. Manual-dispatch only; the operator runs it when they want a snapshot.
- Multi-host support. Single VPS today; the workflow doesn't parameterise host / user.

## Decisions

### D-1: `webfactory/ssh-agent` over inline `ssh-add`

- **Decision**: use the `webfactory/ssh-agent@v0.9.0` action to load the key.
- **Alternatives**: (a) `mkdir ~/.ssh; echo "$KEY" > ~/.ssh/id_ed25519; chmod 600 ~/.ssh/id_ed25519`; (b) `ssh -i <(echo "$KEY") ...` (process substitution).
- **Rationale**: The action handles key format detection (RSA vs ed25519), agent lifecycle, and secret masking in logs. The manual approach frequently leaks key bytes into `set -x` output or fails silently on wrong key type. Pinned to `@v0.9.0` (SHA-check optional; the action is well-audited).
- **Cost of reversal**: LOW.

### D-2: SSH heredoc runs the entire checklist in ONE session

- **Decision**: single `ssh ... bash << 'REMOTE' ... REMOTE` block.
- **Alternatives**: one `ssh` invocation per check (10 round-trips).
- **Rationale**: One SSH handshake vs ten. Also keeps the diagnostic script visually contiguous — easy to read + extend. The heredoc is quoted (`'REMOTE'`) so no local expansion — remote script uses remote's env / paths.
- **Cost of reversal**: LOW.

### D-3: `set +e` inside the heredoc — checks report but don't abort

- **Decision**: individual command failures are informational; the workflow completes even if a check returns non-zero.
- **Alternatives**: `set -e` (bail on first failure).
- **Rationale**: The checklist is DIAGNOSTIC. A missing `hermes` user is signal, not failure. The workflow's job is to REPORT state, not to score it. The final PR review interprets the output.
- **Cost of reversal**: LOW.

### D-4: `concurrency: vps-diagnose` — one run at a time

- **Decision**: only one diagnostic can run at a time.
- **Alternatives**: unlimited concurrency.
- **Rationale**: two parallel diagnostic runs = two SSH sessions to the same VPS = interleaved log output = harder to read. Serialising is trivial + cheap (diagnostic is <1 min).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Workflow runs cleanly on manual dispatch

- **A-1.1**: `gh workflow run vps-diagnose.yml --repo ricardoafo/hermes` schedules a run; `gh run watch` exits 0.
- **A-1.2**: The job log contains 10 numbered sections with `════` separators.

### R-2: Zero side effects on VPS

- **A-2.1**: `grep -E '^\s*(rm|mv|systemctl restart|apt install|pip install|git pull|touch|mkdir|echo.*>)' .github/workflows/vps-diagnose.yml` returns EMPTY (no write commands in the workflow — only reads).
- **A-2.2**: After a run, `journalctl --since "5 minutes ago"` on the VPS shows no modification events (verifiable manually if paranoid).

### R-3: Secret plumbing works end-to-end

- **A-3.1**: The workflow completes without an "SSH: Permission denied" or "Host key verification failed" error.
- **A-3.2**: If it FAILS on the SSH step, the diagnostic OUTPUT for #179's deploy body work is: "prod env's VPS_SSH_KEY is invalid or wrong-user for VPS_SSH_HOST" — that's actionable.

### R-4: Output is structured for later PR-body quoting

- **A-4.1**: Each section starts with `════` and a numbered heading, ends with `════`. Parseable by simple grep for later PR quotes.

## Design

Workflow file layout (full text in the PR):

```yaml
name: VPS diagnose
on: workflow_dispatch
concurrency: { group: vps-diagnose, cancel-in-progress: false }
permissions: { contents: read }

jobs:
  diagnose:
    runs-on: ${{ vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) || 'ubuntu-latest' }}
    environment: prod
    steps:
      - uses: webfactory/ssh-agent@v0.9.0
        with: { ssh-private-key: ${{ secrets.VPS_SSH_KEY }} }

      - name: Add VPS to known_hosts
        env: { VPS_SSH_HOST: ${{ secrets.VPS_SSH_HOST }} }
        run: ssh-keyscan -H "$VPS_SSH_HOST" >> ~/.ssh/known_hosts

      - name: Run diagnostic checklist
        env:
          VPS_SSH_HOST: ${{ secrets.VPS_SSH_HOST }}
          VPS_SSH_USER: ${{ secrets.VPS_SSH_USER }}
        run: |
          ssh "$VPS_SSH_USER@$VPS_SSH_HOST" bash << 'REMOTE'
          # 10 sections:
          # 1. OS + kernel
          # 2. Python + uv
          # 3. /opt/hermes contents + git state
          # 4. Systemd units (hermes*)
          # 5. Runtime user + FHS directories
          # 6. Storage-box mount
          # 7. Disk + memory
          # 8. Firewall (UFW)
          # 9. Fail2ban
          # 10. GH Actions runner
          REMOTE
```

## Test plan

1. `bash scripts/dev/pr-preflight.sh --render-body chore-vps-diagnose-workflow` — all 17 checks green.
2. YAML syntax validation: `python -c "import yaml; yaml.safe_load(open('.github/workflows/vps-diagnose.yml'))"` exits 0.
3. Manual dispatch after merge: `gh workflow run vps-diagnose.yml --repo ricardoafo/hermes` → `gh run watch` → job succeeds → 10 sections visible in log.
4. Operator + agent read the log, use it to draft #179's deploy body against ACTUAL VPS state.

## Risks

- **R1** — SSH connect fails (bad key, host-key mismatch, firewall). Mitigation: `ssh-keyscan` populates known_hosts on the runner; `webfactory/ssh-agent` handles key format. If it fails, the log tells us exactly why — actionable for prod-env secret fix.
- **R2** — Runner's outbound IP is not allowed by the VPS firewall. Mitigation: GitHub-hosted runners come from a documented IP range. If we tightened the VPS's UFW to a specific IP set, we'd need to allow GitHub's range OR route via a self-hosted runner sharing the operator's IP. For diagnostic purposes we'll see the failure and adjust.
- **R3** — `sudo -n ufw status` fails without password. Mitigation: expected on default installs. Section 8 prints "UFW status unavailable (needs sudo)" — harmless, still informative that we couldn't check.
- **R4** — Secret leak in log (e.g. `set -x` echoing `$VPS_SSH_HOST`). Mitigation: workflow uses `env:` block per-step (GitHub auto-masks) and never `set -x` in the run scripts.

## Dependencies on other changes

- Predecessor: `prod` GitHub Environment already exists with `VPS_SSH_HOST`, `VPS_SSH_USER`, `VPS_SSH_KEY` populated (confirmed via `gh api repos/ricardoafo/hermes/environments/prod/secrets`).
- Predecessor: PR #213 (GitHub App auth for automation) — the diagnostic doesn't need it (uses env secrets, not App token) but the pattern is the same.
- Enables: #179 (CD-to-VPS deploy body) — operator + agent use this diagnostic's output to draft the real deploy body against actual VPS state.
- Enables: `docs/guides/vps-setup.md` (currently draft on my other branch) — the runbook needs to know what's already there vs what to install; this diagnostic answers.
