# Prod env file path is /etc/hermes/env.production

**Date**: 2026-10-02
**Author**: AI Bot on behalf of the Code Owner
**Related**: `docs/decisions/2026-08-23-credential-storage-layered-by-env.md`
(supersedes its prod path),
[`openspec/archive/179-cd-first-real-deploy/change.md`](../../openspec/archive/179-cd-first-real-deploy/change.md)
(D-2, D-8),
[`openspec/archive/306-serve-systemd-boot/change.md`](../../openspec/archive/306-serve-systemd-boot/change.md),
GH #179, GH #306

## Decision

The prod EnvironmentFile is `/etc/hermes/env.production`, owned `root:root`
with mode `0600`, replacing the `/etc/hermes.env` that
`docs/decisions/2026-08-23-credential-storage-layered-by-env.md` names.

## Context

The 2026-08-23 entry puts prod credentials in `/etc/hermes.env`. The code
that exists since then uses another path: `hermes.service` (change 306) reads
`EnvironmentFile=/etc/hermes/env.production`, and the `vps-bootstrap`
workflow renders that file. No file in the repo reads `/etc/hermes.env`.

The 2026-08-23 entry is immutable, so it stays as written and this entry
overrides only the path. The three-tier model (dotenv, GH Secrets, systemd
EnvironmentFile) and the env-var names are unchanged.

The bootstrap script (change 179, D-8) sets the owner and mode. The earlier
script wrote `0400 hermes:hermes`, which the service never needed: systemd
reads the file as root before dropping to `User=hermes`, so `hermes` must not
be able to read it. The script writes the file once, when absent. On an
existing file it repairs owner and mode and leaves the content alone, so a
re-dispatch cannot overwrite a hand fix or truncate the file.

## Consequences

- Operators and docs use `/etc/hermes/env.production`. Anything that still
  says `/etc/hermes.env` is stale; the 2026-08-23 entry is the one place that
  keeps the old name, as history.
- `install_release.sh` refuses to deploy unless the file is `600 root:root`
  and does not enable `HERMES_ALLOW_MAINNET`.
- Rotating a secret means editing the file on the box, or deleting it and
  re-dispatching `vps-bootstrap`. Changing the GH `prod` environment alone
  changes nothing on the VPS.
