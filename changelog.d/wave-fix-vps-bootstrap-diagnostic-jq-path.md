### Fixed

- **`.github/workflows/vps-bootstrap.yml`** — Tailscale-self diagnostic step now reads per-device fields from `.Self` (was reading from the top level of `tailscale status --json`, which only mirrors `TailscaleIPs`). Fixes misleading `Tags: null` output that suggested the runner joined untagged when it actually carried `tag:ci-runner`. Followup to #294 — surfaced during 2026-09-06 vps-bootstrap debugging when the false-negative sent us down a wrong-track investigation.
