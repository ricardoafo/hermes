- **`hermes.service` boots `hermes serve` under systemd** (`deploy/systemd/`,
  `src/hermes/__main__.py`): the unit runs the active release's console
  script from `/var/lib/hermes` with `/etc/hermes/env.production`, bounds
  crash loops (no restart on exit 2 or 3, 5 starts per 10 minutes), and
  `python -m hermes` now works. The env example is paper-only and loads
  through the real config loader, both timers parse under systemd, and the
  nightly backup now includes `*.sqlite3` files. Closes #306.
