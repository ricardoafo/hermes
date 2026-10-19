# Change: 1186-ports-and-file-io

**SDD change**: `1186-ports-and-file-io`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1186-ports-and-file-io/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Three findings from the 18 Oct repo audit (D-5, D-6, D-7). The certification
port imports the application layer, because the record it stores lives
there. Six application modules read files directly. Two of them find the
repository by walking up from `__file__`, which fails in an installed
package. This change moves the record into the domain and adds an
import-linter contract so ports name only domain values. It also routes
every application read, hash and append through a new `FilesPort`, and has
the entrypoint or script name the project root. Behaviour does not change:
the same files are read, hashed and appended. The main risk is call-site
churn: every caller of the touched functions now passes the port.

## Scope

### In

- `hermes.domain.calibration.certification`: `CertificationState`,
  `Transition`, `CertificationRecord` (moved, unchanged).
- `hermes.ports.files.FilesPort` and `hermes.adapters.files.LocalFiles`.
- Each of these takes a keyword `files: FilesPort`: `evidence_files`,
  `signed_gate`, `batch_stage`, `research_batch.load_batch`,
  `day_zero.notebook_check` / `sha256_tree`, `LabNotebook` and
  `PreRegistration`. `day_zero.sha256_file` becomes `FilesPort.sha256`.
- `PreRegistration(project_root, *, files)` and `LabNotebook(path, *, files)`
  take their location from the caller.
  `hermes.infrastructure.project_root` holds the walk to `pyproject.toml`
  (`find_project_root`) and `load_pre_registration(root=None)` for
  entrypoints, scripts and tests.
- `judge_candidate` and `judge_stage` take the v1 pre-registration instead of
  building it per candidate.
- Contract `ports-import-only-domain`, and a test that no application module
  calls `open()` or a `Path` file-system method.

### Out

- The optional `layers` contract the issue mentions: the forbidden contracts
  already cover each pair, and a layers contract would also forbid the
  infrastructure-to-adapters imports the config loader makes today.
- Entrypoint and adapter file IO: those layers own IO.

## Decisions

### D-1: The record moves to the domain, the gates stay

- **Decision**: Only the three plain value types move. `GateReport`,
  `GateEvidence` and the gate functions stay in
  `application.calibration.certification_gates`.
- **Alternatives**: Move the whole module (it reads `PreRegistration` and
  `swing_parameters`, which are not domain); keep the record and let the
  port use `TYPE_CHECKING` (that is the finding).
- **Rationale**: The store port and its JSON adapter need only the record.
- **Cost of reversal**: LOW.

### D-2: One small files port, injected by keyword

- **Decision**: `FilesPort` has eight methods: `read_bytes`, `read_text`,
  `exists`, `is_dir`, `append_text`, `sha256`, `files_under` and `resolve`.
  Every application function that touches a file takes `files=` as a
  required keyword.
- **Alternatives**: Separate `TextSourcePort` and `EvidenceStorePort` (two
  ports for one disk); parse bytes passed in by the caller (moves the reads
  but not `batch_stage`'s folder check or `day_zero`'s tree walk).
- **Rationale**: The issue asks for reads and appends through a port; one
  port keeps the fake and the adapter small.
- **Cost of reversal**: LOW.

### D-3: The caller names the project root

- **Decision**: `PreRegistration` and `LabNotebook` no longer detect the
  repository. `find_project_root()` in infrastructure keeps the walk to
  `pyproject.toml` for checkouts and scripts. `serve.pre_registration_for`
  still prefers the folder beside `config/` (issue #923).
- **Alternatives**: Keep a default in the application (the finding); read the
  file from package data (the signed file is not packaged).
- **Rationale**: An installed release has no checkout, so the walk belongs
  to the code that knows where it runs.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Ports import only the domain

- **A-1.1**: `uv run lint-imports` keeps `ports-import-only-domain` (15 contracts kept).

### R-2: The application touches no file itself

- **A-2.1**: `uv run pytest tests/unit/test_application_file_io.py` exits 0. The
  guard flags the four `read_bytes` calls of `evidence_files.py` on the base
  commit.

### R-3: Behaviour is unchanged

- **A-3.1**: `uv run pytest tests/unit tests/integration tests/contract` exits 0,
  with no assertion changed. Only call sites gain `files=` / `v1=`, and the
  record imports move.
- **A-3.2**: `uv run pytest tests/unit/adapters/files tests/unit/infrastructure/test_project_root.py tests/unit/domain/calibration/test_certification.py` exits 0.

## Design

```text
src/hermes/domain/calibration/certification.py   new   CertificationState, Transition, CertificationRecord
src/hermes/ports/files.py                        new   FilesPort
src/hermes/adapters/files/local_files.py         new   LocalFiles
src/hermes/infrastructure/project_root.py        new   find_project_root, load_pre_registration
src/hermes/application/calibration/*             edit  files= on every read
src/hermes/application/strategy_protocol/*       edit  files=, caller-named paths
src/hermes/entrypoints/{certify,funnel,funnel_batch,serve,shadow_start}.py  edit  pass LocalFiles
scripts/backtest/*, scripts/data/*               edit  load_pre_registration(), LocalFiles
pyproject.toml                                   edit  ports-import-only-domain
```

Run against the current tree:

```text
$ uv run lint-imports
Contracts: 15 kept, 0 broken.
$ uv run mypy src
Success: no issues found in 483 source files
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.
4. Code Owner review.

Test tiers touched: unit, property

## Risks

- **Call-site churn**: any caller outside this tree that builds
  `PreRegistration()` with no root breaks. Every caller in `src/`, `scripts/`
  and `tests/` is updated; mypy strict checks `src/`.
- **Append semantics**: `LocalFiles.append_text` opens the file `O_APPEND`
  without `O_CREAT`, so it refuses a missing one. Before, `open("a")` could create it, but
  `LabNotebook` already refused a missing notebook at construction.
- **Open PRs**: PR #1250 drops `from __future__ import annotations` in files
  this change touches. Whichever merges second merges main in.

## Dependencies on other changes

- None. No ADR changes.
