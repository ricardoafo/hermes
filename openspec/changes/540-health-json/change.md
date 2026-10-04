# Change: 540-health-json

**SDD change**: `540-health-json`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#540)

## Summary

`hermes health` printed only a table, and `--help` was read as an integration
name. It now takes `--json`, which prints one JSON document with an object per
integration (name, status, detail, latency, last check, exit code) and the
overall exit code, and `-h`/`--help`, which prints usage with the exit codes.
The tables and the exit codes are unchanged.

## Scope

### In

- `--json` on `hermes health` and `hermes health <integration>`.
- `-h`/`--help` (exit 0) and an unknown option (exit 2).
- Masking of secret env values in the JSON `detail`.

### Out

- Moving ERROR lines to stderr (separate issue).
- Any change to the probes or the table output.

## Decisions

### D-1: Mask secrets by env value, not by probe

- **Decision**: `render_json` replaces every env value whose key looks like a
  secret (TOKEN, SECRET, PASSWORD, DSN, KEY, PING_URL) inside `detail`.
- **Rationale**: a probe error can echo a URL that carries a token; masking at
  the output boundary covers every present and future probe.
- **Alternatives**: auditing each probe's messages (drifts as probes are added).
- **Cost of reversal**: LOW.

## Requirements

### R-1: JSON output

- **A-1.1**: `uv run pytest tests/unit/entrypoints/commands/test_health_json.py`
  (one object per integration, aggregate exit code, both forms).

### R-2: No secrets in output

- **A-2.1**: same file, including a property test over arbitrary secret values.

### R-3: Usage and exit codes

- **A-3.1**: same file (`-h`/`--help` returns 0, unknown option returns 2, table
  form unchanged); `tests/unit/entrypoints/test_health.py` and `test_cli.py`
  pass unmodified.

## Design

```text
entrypoints/_health.py            render_json(results, env, stream) -> exit code
entrypoints/commands/health.py    _parse(): --json, -h/--help, unknown option -> 2
```

## Test plan

1. Probes faked; no network.
2. Tiers: unit and property.

## Risks

- A secret that the probe transforms (for example base64) is not masked; the
  current probes only echo raw values.

## Dependencies

None.
