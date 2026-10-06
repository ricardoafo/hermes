# Secret rotation

**Last exercised**: never (written 2026-10-06; drill on first rotation)
**Owner**: Ricardo
**Related**: AGENTS.md rule 6, issue #679, issue #768 (prod-environment
scoping), [`vps-restart-procedure.md`](vps-restart-procedure.md)

Every credential hermes uses lives in three places, never in the repo:

| Copy | Where | Read by |
| ---- | ----- | ------- |
| GitHub | `prod` environment secrets (main-only) | scheduled contract tests, CD, VPS bootstrap |
| VPS | `/etc/hermes/env.production` on hermes-01 | `hermes serve` and the timers |
| Local | `.env.local` in your clone | `hermes health`, local runs |

A rotation is done when all three hold the new value, the old one is
revoked at the provider, and the probes below pass.

## Symptom

Any of:

- `bash scripts/dev/secret-ages.sh` lists a secret older than its limit.
- A provider says a key leaked, or a key showed up in a log, a PR or a
  screen share.
- `hermes health` reports `unauthorized` / `401` for one integration.
- Someone who had access no longer should.

## Diagnosis

```bash
# 1. Which GitHub copies are due? (reads names and dates only, never values)
bash scripts/dev/secret-ages.sh
# Exit 0 -> nothing is older than 90 days. Exit 1 -> the listed ones are due.

# 2. Does the VPS copy still work?
ssh hermes-01 'sudo bash -c "set -a; . /etc/hermes/env.production; set +a; \
  cd /var/lib/hermes && runuser -u hermes -- /opt/hermes/current/.venv/bin/hermes health"'
# Every integration OK -> the VPS copy is good; rotate on schedule.
# One integration unauthorized -> rotate that one now.
```

A leaked secret skips the schedule: rotate it now, starting at step 1 of
Recovery.

## Recovery

Rotate one secret at a time. Pick a moment between 4h bar closes
(`hermes status` shows the next one).

### 1. Mint the new value at the provider

| Secret | How to rotate it |
| ------ | ---------------- |
| `HERMES_TELEGRAM__BOT_TOKEN` | @BotFather, `/revoke`, pick the bot. The old token dies at once. |
| `HERMES_SLACK__BOT_TOKEN` | api.slack.com, the app, OAuth & Permissions, rotate the bot token (or reinstall the app). |
| `HERMES_SENTRY__DSN` | Sentry project settings, Client Keys (DSN), create a key, then disable the old one in step 5. |
| `HERMES_HEALTHCHECKS__PING_URL` | healthchecks.io check, Settings, regenerate the ping URL (or create a new check and delete the old one in step 5). |
| `HERMES_KRAKEN__API_KEY` / `__API_SECRET` | Kraken, Security, API, create a key with the same permissions (query only, no trading, no withdrawal). Delete the old one in step 5. |
| `HERMES_BINANCE_TESTNET__API_KEY` / `__API_SECRET` | testnet.binance.vision, generate a new HMAC key. Delete the old one in step 5. |

Never paste a value into a chat, an issue, a PR or a commit.

### 2. Update the GitHub copy

```powershell
gh secret set HERMES_TELEGRAM__BOT_TOKEN --env prod --repo ricardoafo/hermes
```

It prompts for the value. Use the secret's name from the table.

### 3. Update the VPS copy

```bash
ssh hermes-01
sudo -e /etc/hermes/env.production   # replace the one line, save
sudo systemctl restart hermes
sudo systemctl is-active hermes      # -> active
```

### 4. Update your local copy

Edit `.env.local` in your clone and replace the same line.

### 5. Revoke the old value at the provider

Only after steps 2 to 4, so nothing is left reading a dead credential.
Telegram's `/revoke` already did this in step 1.

### 6. Verify

```bash
# VPS: every integration OK
ssh hermes-01 'sudo bash -c "set -a; . /etc/hermes/env.production; set +a; \
  cd /var/lib/hermes && runuser -u hermes -- /opt/hermes/current/.venv/bin/hermes health"'

# Local: the same probes against .env.local
uv run hermes health

# GitHub: the contract tests read the prod copy (Kraken, Telegram, Sentry)
gh workflow run scheduled.yml --ref main --repo ricardoafo/hermes
```

Then `bash scripts/dev/secret-ages.sh` shows the rotated secret at age 0.

## Post-incident review

Open an issue after every unscheduled rotation:

- What leaked, and how?
- How long was the old value live after the leak?
- Did `secret-ages.sh` or `hermes health` catch it, or a person?
- Is there a code or process fix that prevents the next one?

## Prevention

- Secrets reachable only from main: issue #768.
- No secrets in the repo, even fake ones without `# gitleaks:allow`:
  AGENTS.md rule 6, gitleaks in pre-commit and CI.
- Scheduled check: run `scripts/dev/secret-ages.sh` monthly. It exits 1
  when anything in the `prod` environment is older than 90 days.
