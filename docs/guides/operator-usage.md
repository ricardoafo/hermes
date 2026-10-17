# Operator Usage — Checking On Hermes Day to Day

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole operator)
**Applies to**: the operator using hermes from a Windows PC (PowerShell in
the repo's `.venv`) and the VPS `hermes-01` (`hermes serve` in shadow mode).
**Description**: Day-to-day use: connect with `ssh hermes` or `hermes --vps`, read the console commands, see the live release, how auto-deploy works, and where to look when something is wrong.

This is the page to open when you want to know "is hermes OK, and what did
it do?". It covers the commands that exist on `main` today. Setup and
recovery live in [vps-operations.md](vps-operations.md); the deploy
machinery lives in [`deploy/README.md`](../../deploy/README.md).

Commands in PowerShell blocks run on your PC, one per line. Commands in
`bash` blocks run on the VPS after you connect.

## The daily loop

```mermaid
flowchart LR
    T["Telegram: alerts and the bot"] -->|something looks off| ST["hermes --vps status"]
    ST -->|all good| A["positions, activity"]
    ST -->|late or down| S["ssh hermes, then hermes_run logs"]
    S --> D["vps-diagnose or rollback"]
```

Most days Telegram is enough: it stays quiet while hermes is healthy and
alerts when it is not (see [When something is wrong](#when-something-is-wrong)).
To look closer, run a console command from your PC with `--vps`, or connect
to the VPS for the logs and the other tools.

## Connect to the VPS

The VPS is reachable only over Tailscale. With the `hermes` alias in your
SSH config, one word connects:

```powershell
ssh hermes
```

The alias lives in `$HOME\.ssh\config` on your PC:

```text
Host hermes
    HostName hermes-01
    User root
    IdentityFile ~/.ssh/hermes_vps_ed25519
```

The long form does the same without the alias:

```powershell
ssh -i $HOME\.ssh\hermes_vps_ed25519 root@hermes-01
```

The key has a passphrase; an SSH agent (Bitwarden's, or the Windows
OpenSSH agent) saves typing it each time. If the connection hangs, check
that Tailscale is running on your PC; see
[vps-operations.md § Access](vps-operations.md#access) for host key
warnings and the public-IP fallback.

### The `hermes_run` helper (one time)

The service runs as user `hermes` with its env file and ledger. The console
commands must run the same way to see the same data, so the VPS uses a
small shell function, `hermes_run`, from
[vps-operations.md § Check on hermes](vps-operations.md#check-on-hermes).
Add it to root's `.bashrc` once, so every `ssh hermes` session has it:

```bash
cat >> /root/.bashrc <<'EOF'
hermes_run() {
    sudo bash -c 'set -a; . /etc/hermes/env.production; set +a; cd /var/lib/hermes &&
        exec runuser -u hermes -- /opt/hermes/current/.venv/bin/hermes "$@"' _ "$@"
}
EOF
source /root/.bashrc
```

`hermes_run` always runs the live release (`/opt/hermes/current`) from
`/var/lib/hermes`, so commands that default to `./hermes.sqlite3` read the
service ledger.

### Plain `hermes` on the VPS

Every deploy since PR #635 also installs `/usr/local/bin/hermes`, a wrapper that does
what `hermes_run` does for the console commands only: `tui`, `status`,
`positions`, `activity` and `money` (plus `--help`). It points them at the
service ledger and config, so they need no `--db` or `--config-dir`:

```bash
hermes status
hermes positions
```

Anything else (`logs`, `version`, `tax`, `ledger verify`, `config check`)
is refused by the wrapper with exit code 2; run those through `hermes_run`.

Every command in this guide is read-only: none of them changes the ledger,
restarts anything or places an order.

## From your PC without logging in: `--vps`

With Tailscale connected, `--vps` runs one console command on the VPS over
SSH and shows it in your terminal, the same as running it there:

```powershell
uv run hermes --vps status
uv run hermes --vps tui
uv run hermes --vps positions BTC
uv run hermes --vps activity --all
```

`--vps` goes either before or after the command (`uv run hermes tui --vps`
does the same as the second line). It takes the same commands as the VPS
wrapper (`tui`, `status`, `positions`, `activity`, `money`) and cannot be
combined with `--demo`. It
connects as `root@hermes-01` with `~/.ssh/hermes_vps_ed25519`; set
`HERMES_VPS_HOST`, `HERMES_VPS_USER` or `HERMES_VPS_KEY` to change that.
The key's passphrase prompt shows as usual. A failure prints one line: no
SSH client, host unreachable (Tailscale down), authentication failed (check
the key), or the VPS build has no console yet (wait for the deploy).
Details: [vps-operations.md § Use the console from your PC](vps-operations.md#use-the-console-from-your-pc).

The sections below show each command as you run it on the VPS. From your
PC, put `uv run hermes --vps` in front of the console ones instead.

## Is it OK? `status`

```bash
hermes status
```

It prints the verdict (all good, late or down), then each stream's last
and next 4h bar. Bars close at 00, 04, 08, 12, 16 and 20 UTC.

Down also covers a server clock that serve found more than the 5 s settle
delay away from Binance's time: "The server clock is off". It stays down
until serve sees the clock back on time at its next bar. Serve sends a P0
alert when the clock goes off, and a quiet one when it comes back.

| Exit code | Meaning |
| --- | --- |
| 0 | All good |
| 1 | Late or down |
| 2 | Bad arguments |
| 3 | Config error or no ledger |

`--json` prints the same as data; `--summary` prints the one-line daily
summary. The healthchecks.io ping runs this same check every 5 minutes.

## The home screen: `tui`

```bash
hermes tui
```

The interactive home screen reads the ledger read-only: the portfolio
total in EUR (with the date of the ECB rate it uses), open positions, a market watch, recent activity and the
shadow test's day count toward 90. The top line names the running release,
read from the same link `hermes status` reads. The market watch lists each
checked coin with its last check, whether that check produced an entry or
exit signal, and the next check; it shows no prices. Keys: `D`, `W`, `M`
and `Y` switch the range (day, week, month, year), the arrow keys (or
`j`/`k`) and Enter open a position, Esc closes it, `L` shows logs, `?`
help and `Q` quits. A figure without an EUR value shows "EUR pending", and
a card the ledger cannot answer yet says "not available yet". `--db PATH`
reads another ledger.

## What does it hold and what did it do?

```bash
hermes positions
hermes positions BTC
hermes activity
hermes activity --all --days 2
hermes money --range week
```

| Command | Shows | Useful options |
| --- | --- | --- |
| `positions` | Open shadow positions, net per row; `positions COIN` for one coin's detail | `--json`, `--utc`, `--db PATH` |
| `activity` | What changed, grouped by day (default: the last 7 days) | `--all` adds bar checks and signals, `--days N`, `--json`, `--utc` |
| `money` | Portfolio total in EUR and the gross-to-net waterfall | `--range today\|week\|month\|year`, `--json`, `--utc` |

Times show in Europe/Madrid unless you pass `--utc`. With no ledger file,
`positions` and `money` answer with an empty state, while `activity` exits
with code 3 naming the missing ledger.

## Logs: `logs`

```bash
hermes_run logs --level warning --since 1h
hermes_run logs --last 100
hermes_run logs --cycle K7Q2ZP
hermes_run logs -f
```

One readable line per journal entry of `hermes.service`. `--level`
filters from that level up (default `debug`), `--since` takes `30m`, `1h`,
`2d` and so on, `--cycle` keeps one bar cycle by the tail of its
correlation id, and `-f` follows until Ctrl-C. Each processed bar logs a
`serve.bar.processed` line:

```bash
hermes_run logs --since 4h | grep serve.bar.processed
```

On a 4h bar checked for an entry, the line also says which of the three
pre-registered entry conditions held: `entry_breakout`, `entry_momentum`
and `entry_regime`, each `yes` or `no`. A signal needs all three.

`logs` reads the systemd journal, so it works on the VPS only.

## Which release is live? `version`

```bash
hermes_run version
```

It prints the package version, the live release (the 12-character commit
`/opt/hermes/current` points at), the Python version and the ledger's
latest migration with the pending count. Without the helper:

```bash
readlink /opt/hermes/current
```

The release name is the first 12 characters of the `main` commit, so you
can find it on GitHub as `https://github.com/ricardoafo/hermes/commit/<release>`.

## Tax estimates: `tax` and `tax-filing`

```bash
hermes_run tax --config-dir /opt/hermes/current/config
hermes_run tax --config-dir /opt/hermes/current/config --year 2026 --json
hermes_run tax-filing --year 2026 --config-dir /opt/hermes/current/config
```

`tax` prints the estimated tax on hermes gains for one Madrid tax year,
labelled shadow. It reads your taxpayer profile from
`~/.config/hermes/taxpayer.yaml` (or `HERMES_TAXPAYER_PROFILE`). Your
figures stay on your PC, so on the VPS there is no profile: it assumes no
other income and says so. `tax-filing` prints the
figures for Modelo 100 and Modelo 721 for your gestor; `--out DIR` also
writes `report.md` and CSV files. Neither submits anything.

## Try the console on your PC: demo mode

The console commands also run on packaged demo data, from the repo on your
PC, with no VPS and no ledger. Every screen is tagged DEMO DATA.

```powershell
uv run hermes --demo ok-two-positions tui
uv run hermes --demo late status
uv run hermes --demo losing-year money --range year
uv run hermes --demo ok-two-positions
```

| Scenario | What it shows |
| --- | --- |
| `ok-two-positions` | All good: two open positions, tax estimates available |
| `late` | A bar check 12 minutes late |
| `down-stale` | hermes stopped; every number is stale |
| `losing-year` | A losing year: losses show as a tax credit, not zero |
| `tax-pending` | Every tax figure shows as pending |
| `empty` | Nothing yet: no positions, no activity, shadow test not started |

`--demo` goes before the command and works with `tui`, `status`,
`positions`, `activity` and `money`. `hermes --demo SCENARIO` with no
command prints the home screen as static text. Setting
`$env:HERMES_CONSOLE_DEMO = "late"` picks a scenario for every command in
that PowerShell window.

On your PC there is no live ledger, so without `--demo` `positions` and
`money` show the empty state, and `tui`, `activity` and `status` exit with
an error.

`uv run hermes --help` lists every command; `uv run hermes <command> --help`
gives its options and exit codes.

## How a change reaches the VPS

Every PR merged to `main` deploys itself once CI passes on `main`. You
approve the PR; nothing else is needed.

```mermaid
flowchart TB
    M["PR merged to main"] --> CI["CI on main"]
    CI -->|green| G["Release checks: end-to-end serve cycle, contract tests"]
    G --> H["Hold if a 4h bar close is within 5 minutes"]
    H --> B["Build wheel and release bundle"]
    B --> D["Deploy job (prod environment)"]
    D --> SN["Snapshot the ledger"]
    SN --> R["Flip current, restart hermes.service"]
    R --> HG{"Health gate and smoke tests"}
    HG -- pass --> OK["New release live"]
    HG -- fail --> RB["Roll back to previous"]
```

- **Bar-close hold**: a deploy never restarts hermes within 5 minutes of a
  4h bar close; it waits instead.
- **Ledger snapshot**: before the restart the deploy copies the ledger to
  `/var/lib/hermes/backups/pre-<release>.sqlite3`. When the health gate
  fails, the rollback restores it; see [ledger-migrations.md](ledger-migrations.md).
- **Health gate**: the unit must stay active for 30 seconds with the same
  process and log `serve.run.started`. Then smoke tests run over SSH.
- **Rollback**: a failed gate or smoke run switches back to the previous
  release and the CD run fails, so you see it red in Actions.
- **`prod` environment**: if it has required reviewers, the deploy waits
  in Actions under "Review deployments" until you approve.

To see a deploy, open the repository's Actions tab, workflow **CD**. A
manual run (`Run workflow`) defaults to `dry_run=true`, which builds the
bundle and touches nothing on the VPS.

Each deploy installs changed systemd unit and timer files (issue #626). A
new unit still needs the **vps-bootstrap** workflow once to enable it, and
env files stay bootstrap-only; see
[vps-operations.md § Hermes bootstrap](vps-operations.md#hermes-bootstrap-automated).

To roll back by hand from your PC:

```powershell
ssh hermes /opt/hermes/bin/rollback.sh
```

It exits 1 when `current` already equals `previous`. It does not roll back
the ledger.

It also refuses when the ledger holds events newer than `previous` can read
(issue #655). For example, after a release writes schema v2 fill-quality
rows, an older release fails at start. Roll back with the snapshot taken
before that release went live instead:

```powershell
ssh hermes "HERMES_RESTORE_DB=/var/lib/hermes/backups/pre-<release>.sqlite3 /opt/hermes/bin/rollback.sh"
```

Restoring a snapshot drops what hermes recorded since it was taken. To roll
back anyway with the live ledger, set `HERMES_SKIP_SCHEMA_GUARD=1`. A deploy
of an older commit is refused the same way.

## When something is wrong

### Where alerts come from

| Alert | Arrives on | Fires when |
| --- | --- | --- |
| healthchecks.io | Telegram | The 5-minute ping stops, or `status` says late or down (after about 15 minutes) |
| Failure notifier | Telegram | `hermes.service` enters `failed` (start limit hit, or exit code 2 or 3) |
| Crash notice | Slack | Any stop that was not clean, including ones systemd restarts |
| Telegram bot | Telegram | Change-only heartbeat alerts, once a minute |

The Telegram bot (`hermes-bot.service`) also answers `/status`, `/next`,
`/signals`, `/money`, `/pos` and `/logs` from your chat, read-only. It
runs when the Telegram secrets are set on the VPS. Details:
[`deploy/README.md` § Alerting when hermes is down](../../deploy/README.md#alerting-when-hermes-is-down).

### First checks on the VPS

```bash
hermes status
systemctl status hermes.service
hermes_run logs --level warning --since 1h
hermes_run ledger verify --db /var/lib/hermes/hermes.sqlite3
hermes_run config check --config-dir /opt/hermes/current/config
```

`ledger verify` checks the ledger's integrity and event sequence;
`config check` loads the config and secrets the way `serve` does, without
starting anything. Both are safe while hermes runs.

| Symptom | Look at |
| --- | --- |
| `status` says late | `logs --since 4h`: is there a `serve.bar.processed` line for the last close? |
| `status` says down, unit `failed` | `systemctl status hermes.service`, then [vps-operations.md § Systemd unit failed](vps-operations.md#systemd-unit-failed) |
| CD run red | The CD run's "Deploy to VPS" job log; the VPS is already back on the previous release |
| A deploy you expected is missing | Actions: did CI on `main` pass, and is the deploy waiting for a review? |
| Can't connect | Tailscale on your PC; [vps-operations.md § If Tailscale is offline](vps-operations.md#if-tailscale-is-offline) |

### vps-diagnose: a snapshot without connecting

The **VPS diagnose** workflow reads the VPS state and prints it in the run
log: OS, the hermes units, the last hour of the journal, the health probes
and the newest processed bar per stream. It changes nothing. Run it from
the Actions tab (**VPS diagnose**, `Run workflow`), approve its `prod`
review when asked, and read the "Snapshot VPS state" job. Search the log
for `serve.bar.processed` to confirm recent bars.

For a full restart procedure see
[runbook/vps-restart-procedure.md](../runbook/vps-restart-procedure.md).

## Related

- [vps-operations.md](vps-operations.md): access, OS maintenance,
  Tailscale, systemd, the supervisor tools
- [`deploy/README.md`](../../deploy/README.md): units, env file, alerting,
  deploy and rollback internals
- [ledger-migrations.md](ledger-migrations.md): migrations and restoring a
  snapshot
- [pr-flow.md](pr-flow.md): how a PR gets from a branch to production
- [local-setup.md](local-setup.md): setting up the repo on a new PC
