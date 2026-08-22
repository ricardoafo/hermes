# Runbook: VPS restart procedure

**Type**: Runbook (incident-response entry per ADR-009 §4)
**Owners**: Code Owner
**Incident**: VPS `hermes-01` needs a restart (planned maintenance, hung
service, kernel update, or unresponsive process)

## Symptom

One or more of:

- `healthchecks.io` reports the dead-man's-switch missed (Telegram alert
  fires — see ADR-008 §Observability)
- `ssh hermes-01` hangs or times out (via Tailscale — port 22 is CLOSED
  to public internet per `deploy/README.md`)
- `systemctl status hermes-*` on the VPS reports services in a `failed`
  or `degraded` state
- `journalctl -u hermes-* --since "5 minutes ago"` shows a wall of
  errors, exit codes, or OOM messages

## Diagnosis

Before restarting anything, confirm what's actually wrong. Restarting a
healthy service resets useful state (backoff counters, in-flight
correlation IDs). Don't restart on a hunch.

```bash
# 1. Can you reach the VPS at all?
ssh hermes-01 uptime         # via Tailscale
# If this hangs → Tailscale connectivity is broken, not the VPS.
# Check `tailscale status` locally first.

# 2. Which services are actually degraded?
ssh hermes-01 'systemctl --failed'
ssh hermes-01 'systemctl status hermes-*'

# 3. What's in the last 5 min of logs?
ssh hermes-01 'journalctl -u hermes-* --since "5 minutes ago" -p err'

# 4. Is the VPS itself stressed?
ssh hermes-01 'top -bn1 | head -20; free -h; df -h /'
# Look for: OOM kills, disk full, load > cpu count
```

If step 1 hangs and Tailscale is up: the VPS itself is unresponsive.
Escalate to a hosting-provider hard reboot (see § Recovery below,
sub-step "hard reboot").

If step 2-4 shows a specific service degraded but the OS is healthy:
restart JUST that service. Do not restart the box.

## Immediate action (first 5 minutes)

### Case A: single service degraded

```bash
# Preserve state — snapshot the ledger before any restart
ssh hermes-01 'sudo -u hermes hermes backup --snapshot --reason "restart"'
# ↑ Phase 0+ command; falls back to `sqlite3 .backup` if the CLI isn't
# there yet.

# Restart just the affected service
ssh hermes-01 'sudo systemctl restart hermes-<service>.service'

# Verify it came back
ssh hermes-01 'systemctl status hermes-<service>.service'
ssh hermes-01 'journalctl -u hermes-<service> --since "1 minute ago"'
```

### Case B: whole VPS restart

```bash
# 1. Pause active trading FIRST (per ADR-010 § CVE-response playbook
#    convention — never restart while the rebalancer/swing engines
#    have live orders)
ssh hermes-01 'sudo -u hermes hermes engine pause --reason "vps restart"'

# 2. Wait for confirmation that in-flight orders settled
ssh hermes-01 'sudo -u hermes hermes engine status'
# Expected: no `IN_FLIGHT` orders. If any, wait 30s and re-check.
# If any are still IN_FLIGHT after 2 minutes, do NOT proceed — escalate.

# 3. Take a backup
ssh hermes-01 'sudo -u hermes hermes backup --snapshot --reason "vps restart"'

# 4. Restart cleanly
ssh hermes-01 'sudo systemctl reboot'
```

The `pause → wait → backup → reboot` sequence is non-negotiable. Skipping
any step risks lost orders or ledger inconsistency at boot.

## Recovery (next 60 minutes)

### Verify the box came back up

```bash
# Wait for Tailscale to reconnect (~30s post-boot typical)
until ssh hermes-01 uptime 2>/dev/null; do sleep 5; done

ssh hermes-01 'uptime'                # confirm recent boot
ssh hermes-01 'systemctl --failed'    # nothing should be listed
ssh hermes-01 'systemctl status hermes-*'   # all `active (running)` or
                                            # `active (waiting)` (timers)
```

### Verify services are healthy

```bash
# 1. `hermes-health` ping (dead-man's-switch)
ssh hermes-01 'systemctl status hermes-health.timer hermes-health.service'
# Timer should be `active (waiting)`, next-elapse soon.
# The service should have fired at least once recently.

# 2. Ledger integrity
ssh hermes-01 'sudo -u hermes hermes ledger verify'
# Expected: `OK — N events, 0 gaps, 0 mismatches`
# Any error → do NOT resume engines. Escalate.

# 3. Config sanity
ssh hermes-01 'sudo -u hermes hermes config check'
# Expected: no missing secrets, no invalid values.
```

### Resume trading

Only after every step above is green:

```bash
ssh hermes-01 'sudo -u hermes hermes engine resume'
ssh hermes-01 'sudo -u hermes hermes engine status'
# Expected: `RUNNING`, next-tick within cadence.
```

### Hard reboot (if the VPS is truly unresponsive)

If SSH via Tailscale is dead AND `tailscale status` locally confirms
peer is offline for >2 minutes, the VPS is unresponsive. Go to the
hosting-provider console (Hetzner web UI) and:

1. **Try graceful reboot first** (Reboot button, not Power Off).
2. **Wait 2 minutes** for reboot cycle.
3. If still unreachable, **request Rescue mode** via the console,
   mount the filesystem, and check `dmesg` + last journal for
   root cause. See § Post-incident.

Do NOT force a power-cycle without first attempting graceful reboot.
SQLite WAL corruption from ungraceful shutdown is recoverable but slow.

## Post-incident

Within 24 hours of any VPS restart:

1. **Decisions-journal entry** documenting:
   - Timestamp (start of degradation → resume trading)
   - Symptom that prompted the restart
   - Root cause (if identified) or `unknown — under investigation`
   - Which services were restarted (single or whole box)
   - Any orders/state lost or mismatched
   - Follow-up actions if root cause was infrastructural (kernel bug,
     disk failure, memory leak — file GH issue)

   Format: `docs/decisions/YYYY-MM-DD-vps-restart-<slug>.md`

2. **If the restart was NOT clean** (orders in flight, ledger mismatch,
   OOM): file a P1 GH issue with the incident timeline. Blocks release
   until root-caused.

3. **If a service pattern recurs** (same service failing weekly, etc.):
   file P2 issue to instrument the failure mode → alert BEFORE it needs
   a restart.

## Prevention

- **healthchecks.io dead-man's-switch** (per ADR-008) — 5-minute
  cadence. Silent means degraded. Runs before this runbook fires.
- **journal rotation + disk monitoring** (Phase 0+) — daily
  systemd-tmpfiles + weekly `journalctl --vacuum-time=30d`. Prevents the
  "disk full → services die" restart cause.
- **memory limits per unit** (Phase 0+) — `MemoryHigh=` in systemd unit
  files. OOM-kills a single service instead of the whole box.
- **kernel-update automation** — `unattended-upgrades` for security
  patches, weekly `apt full-upgrade` in a maintenance window (Phase 4+
  when release cadence stabilizes).
- **Quarterly drill** — first Monday of each quarter, run this runbook
  end-to-end on a maintenance window. Confirms it still works, updates
  any drift. Drill result → decisions-journal entry per ADR-009 §4.

## Related

- [ADR-003 §09 Deployment](../architecture/adr-003-solution-architecture/09-deployment.md) — VPS provisioning + systemd unit design
- [ADR-008 Observability](../architecture/adr-008-observability-strategy.md) — dead-man's-switch, journald, Telegram alerts
- [ADR-010 CVE response playbook](../architecture/adr-010-dependency-management-and-supply-chain.md) — pause → backup → patch → resume sequence (this runbook uses the same pattern)
- [`deploy/README.md`](../../deploy/README.md) — VPS provisioning + secrets required
