- **Telegram alert when `hermes.service` is down** (`deploy/systemd/`,
  `deploy/hermes-notify-failure.sh`, `deploy/hermes-health-ping.sh`):
  `hermes.service` gets `OnFailure=`, which sends one Telegram message
  (unit, host, last five journal lines with secrets masked) when the unit
  lands in `failed`. `hermes-health.service` now pings healthchecks.io only
  while the service is active, reads the URL from `/etc/hermes/env.production`
  and no longer swallows a failed ping, so a missed ping means hermes is down.
  Closes #312.
