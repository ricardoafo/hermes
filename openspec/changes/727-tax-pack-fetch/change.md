# Change: 727-tax-pack-fetch

**SDD change**: `727-tax-pack-fetch`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/727-tax-pack-fetch/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes tax-pack fetch --year YYYY` copies the server's tax pack to the PC over
the ssh connection the console already uses, checks every file against the
pack's `manifest.json`, and only then writes `<out>/server/<year>/`. The
taxpayer opens `renta-web.pdf` from there. Nothing is uploaded, nothing is
submitted, and the console stays read-only. Whether the server's
`hermes-tax-pack.timer` is enabled is not part of this change: it is a server
change that stays the Code Owner's call.

## Scope

### In

- `hermes.entrypoints.tax_pack_fetch` and the `fetch` word on the existing `tax-pack` command.
- A short section in `docs/guides/tax-pack.md`.

### Out

- Enabling `hermes-tax-pack.timer` or any server change.
- The console Tax screen showing the pack status and the fetch command (a console change; follow-up).
- Merging the server pack with a local run that has the taxpayer profile.

## Decisions

### D-1: A tar stream over ssh, read in memory

- **Decision**: run `tar -C /var/lib/hermes-tax-pack -cf - <year>` over ssh and read the archive from stdout.
- **Alternatives**: `scp -r` (not on every Windows install, no single stream to check); a zip built on the server (needs a server-side change); `rsync` (not on Windows).
- **Rationale**: only `ssh` is needed on the PC, on Windows PowerShell and Linux alike; the archive is read by Python's `tarfile`, so nothing is extracted by a local `tar`.
- **Cost of reversal**: LOW.

### D-2: A separate folder, `<out>/server/<year>/`

- **Decision**: the fetched pack never lands in `<out>/<year>/`.
- **Alternatives**: replace `<out>/<year>/` (would delete a local pack that holds `tax-estimate.txt` from the taxpayer profile).
- **Rationale**: a local run with the profile and a fetched server pack can both exist, and neither overwrites the other.
- **Cost of reversal**: LOW.

### D-3: Verify before writing, accept only flat regular files

- **Decision**: the archive must hold only regular files directly under `<year>/`; the file set must equal the manifest's list plus the manifest; every sha256 must match; `tax_year` must equal the asked year. Any failure writes nothing and exits 4. The new folder is staged next to the destination and swapped in, so a failed refetch keeps the earlier copy.
- **Rationale**: a manifest mismatch fails loudly (the issue's acceptance), and a hostile or broken stream cannot write outside the folder.
- **Cost of reversal**: LOW.

### D-4: Reuse the console's ssh settings

- **Decision**: `HERMES_VPS_HOST`, `HERMES_VPS_USER` and `HERMES_VPS_KEY` as for `--vps`; ssh runs with `BatchMode=yes`. A host or user that is not a plain name is refused (exit 2).
- **Rationale**: one place to configure the connection; an option-shaped host cannot reach ssh.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Fetch

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_tax_pack_fetch.py` passes (a verified pack is written byte for byte, a refetch replaces the earlier copy, the ssh argv is the fixed tar command).

### R-2: Verify

- **A-2.1**: the same file covers a changed byte, a missing file, an unlisted file, a missing or foreign-year manifest, a path outside `<year>/`, a link, and a stream that is not a tar: each exits 4 and writes nothing; a failed refetch keeps the earlier copy.

### R-3: Failure and usage

- **A-3.1**: the same file covers an ssh failure (exit 1 with the reason), a year with no pack, a missing ssh client, a bad command line and an option-shaped host (exit 2).

## Design

- `fetch_main(argv, *, stream, default_out, env, runner, err)`; `runner` is the only seam (ssh), so the tests build archives with the production `render_manifest`.
- `commands/tax_pack.py` sends `tax-pack fetch ...` to `fetch_main` and everything else to the refresh.
- Verified on the current tree: `uv run mypy` (strict, no issues), `uv run ruff check .`.
- Not verified here: a real ssh run against `hermes-01` (agents do not touch the server); the first run is the Code Owner's, with the server pack present.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit/entrypoints` passes.
3. Mutation spot checks: dropping the checksum comparison and the path check each fail the new tests.

Test tiers touched: unit, property

## Risks

- **The server has no pack until the timer is enabled**: the command then exits 1 with tar's "No such file" and says so; enabling the timer is the Code Owner's call.
- **A pack larger than 64 MiB is refused**: a real pack is a few hundred KB; the cap bounds memory.

## Dependencies on other changes

- None.
