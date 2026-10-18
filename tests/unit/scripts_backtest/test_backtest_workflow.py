"""Invariants for ``.github/workflows/backtest.yml`` and its runner (ADR-023).

The workflow cannot run outside server-01, so these tests pin what a first
run would otherwise discover: the window it derives from the manifest, the
Validation gate, the trial lines it hands to the follow-up PR, and the
runner it is routed to. The window and trial-line tests run the steps' own
shell against temporary files; the real data, driver and runner are
exercised by the first dispatch on server-01.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github" / "workflows" / "backtest.yml"
_COMPOSE = _ROOT / "scripts" / "dev" / "gh-runner" / "compose.yml"
_MANIFEST = _ROOT / "docs" / "experiments" / "swing-mvp1" / "data-manifest.json"
_SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w.-]+)*@[0-9a-f]{40}$")

needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the steps' shell needs bash and jq, which the runner image has",
)


def _workflow() -> dict[Any, Any]:
    """Parse backtest.yml."""
    loaded = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _job() -> dict[str, Any]:
    """Return the backtest job."""
    return dict(_workflow()["jobs"]["backtest"])


def _step(name: str) -> dict[str, Any]:
    """Return one step of the backtest job by name."""
    return next(s for s in _job()["steps"] if s.get("name") == name)


def _run_step(name: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a step's shell as the runner does (``bash -e``) with ``env`` on a minimal environment."""
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run(  # noqa: S603 — fixed interpreter, the workflow's own script
        ["bash", "-e", "-c", str(_step(name)["run"])],  # noqa: S607 — bash from PATH, as on the runner
        env={**base, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _window(tmp_path: Path, split: str, ref: str = "refs/heads/main") -> tuple[int, dict[str, str]]:
    """Run the window step for ``split`` and return its exit code and outputs."""
    (tmp_path / "docs" / "experiments" / "swing-mvp1").mkdir(parents=True, exist_ok=True)
    shutil.copy(_MANIFEST, tmp_path / "docs" / "experiments" / "swing-mvp1" / "data-manifest.json")
    output = tmp_path / "github_output"
    output.write_text("")
    result = _run_step(
        "Window from the manifest",
        {"SPLIT": split, "REF": ref, "EXPERIMENT": "swing-mvp1", "GITHUB_OUTPUT": str(output)},
        tmp_path,
    )
    pairs = (line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return result.returncode, dict(pairs)


@needs_tools
def test_train_window_is_the_manifest_train_split(tmp_path: Path) -> None:
    """A train run evaluates exactly Train, so it never counts as a Validation touch."""
    code, out = _window(tmp_path, "train")

    assert (code, out["start"], out["end"]) == (0, "2020-09-01", "2024-06-30")


@needs_tools
def test_validation_window_matches_the_8_october_gate(tmp_path: Path) -> None:
    """A validation run spans Train start to Validation end, as the signed gate ran."""
    code, out = _window(tmp_path, "validation")

    assert (code, out["start"], out["end"]) == (0, "2020-09-01", "2025-08-31")


@needs_tools
def test_oos_split_is_refused_before_any_data(tmp_path: Path) -> None:
    """An ``oos`` split, even if forced through the API, never yields a window."""
    code, out = _window(tmp_path, "oos")

    assert (code, out) == (1, {})


@needs_tools
@pytest.mark.parametrize("split", ["train", "validation"])
def test_any_split_from_a_branch_is_refused(tmp_path: Path, split: str) -> None:
    """Only main's reviewed code runs on server-01 and logs a trial, for either split."""
    code, out = _window(tmp_path, split, ref="refs/heads/feature")

    assert (code, out) == (1, {})


@needs_tools
def test_window_reaching_oos_start_is_refused(tmp_path: Path) -> None:
    """A manifest whose Validation end reaches the OOS start fails the run."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["splits"]["validation"]["end"] = manifest["splits"]["oos"]["start"]
    target = tmp_path / "docs" / "experiments" / "swing-mvp1"
    target.mkdir(parents=True)
    (target / "data-manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "github_output"
    output.write_text("")
    env = {"SPLIT": "validation", "REF": "refs/heads/main", "EXPERIMENT": "swing-mvp1"}

    result = _run_step("Window from the manifest", {**env, "GITHUB_OUTPUT": str(output)}, tmp_path)

    assert result.returncode == 1


@needs_tools
def test_new_trial_lines_are_exactly_the_appended_bytes(tmp_path: Path) -> None:
    """The artifact carries only what this run appended, byte for byte, for the follow-up PR."""
    committed = tmp_path / "trial-log.jsonl"
    committed.write_text('{"n": 1}\n')
    runner_temp = tmp_path / "rt"
    (runner_temp / "out").mkdir(parents=True)
    (runner_temp / "trial-log.jsonl").write_text('{"n": 1}\n{"n": 2}\n')
    env = {
        "RUNNER_TEMP": str(runner_temp),
        "SRC": str(committed),
        "BASE_BYTES": str(committed.stat().st_size),
    }

    result = _run_step("New trial lines", env, tmp_path)

    assert result.returncode == 0
    assert (runner_temp / "out" / "trial-log.new.jsonl").read_text() == '{"n": 2}\n'


@needs_tools
def test_rewritten_trial_log_history_fails_the_run(tmp_path: Path) -> None:
    """A copy whose start differs from the committed log is never handed on as new lines."""
    committed = tmp_path / "trial-log.jsonl"
    committed.write_text('{"n": 1}\n')
    runner_temp = tmp_path / "rt"
    (runner_temp / "out").mkdir(parents=True)
    (runner_temp / "trial-log.jsonl").write_text('{"n": 9}\n{"n": 2}\n')
    env = {
        "RUNNER_TEMP": str(runner_temp),
        "SRC": str(committed),
        "BASE_BYTES": str(committed.stat().st_size),
    }

    result = _run_step("New trial lines", env, tmp_path)

    assert result.returncode == 1


def test_dispatch_is_the_only_trigger() -> None:
    """Every run is a trial, so nothing but a deliberate dispatch may start one."""
    triggers = _workflow()[True]  # PyYAML reads the `on:` key as True

    assert list(triggers) == ["workflow_dispatch"]


def test_split_choices_exclude_oos() -> None:
    """The dispatch form offers Train and Validation only; OOS is sealed."""
    split = _workflow()[True]["workflow_dispatch"]["inputs"]["split"]

    assert (split["type"], split["options"], split["default"]) == (
        "choice",
        ["train", "validation"],
        "train",
    )


def test_validation_waits_on_the_protected_environment() -> None:
    """A Validation run uses backtest-validation, whose branch rule admits only main."""
    assert _job()["environment"] == (
        "${{ inputs.split == 'validation' && 'backtest-validation' || 'backtest-train' }}"
    )


def test_starter_check_runs_before_any_data_is_read() -> None:
    """Who started a Validation run is checked before data is filled or a trial logged."""
    names = [s.get("name") for s in _job()["steps"]]

    assert names.index("Code Owner started this Validation touch") < names.index(
        "Fill and verify the pinned data"
    )
    assert _step("Code Owner started this Validation touch")["if"] == (
        "${{ inputs.split == 'validation' }}"
    )


def test_runs_only_on_the_backtest_runner() -> None:
    """The job is routed to server-01's runner, never the CI pool, ops runner or VPS."""
    assert _job()["runs-on"] == ["self-hosted", "hermes-backtest"]


def test_runs_never_overlap() -> None:
    """Two runs at once would chain trial lines onto the same parent."""
    assert _workflow()["concurrency"] == {"group": "backtest", "cancel-in-progress": False}


def test_workflow_holds_no_write_permission() -> None:
    """Trial lines leave as artifacts; no token in the workflow can write to the repo."""
    scopes = [_workflow()["permissions"], _job()["permissions"]]

    assert scopes == [{"contents": "read"}, {"contents": "read"}]


def test_checkout_does_not_persist_the_token() -> None:
    """The job token is not left in .git/config for branch code to reuse."""
    checkout = next(
        s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )

    assert checkout["with"]["persist-credentials"] is False


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    """A moved tag cannot change what runs (ADR-019 T2)."""
    uses = [str(s["uses"]) for s in _job()["steps"] if "uses" in s]

    assert [u for u in uses if not _SHA_PIN.match(u)] == []


def test_inputs_never_reach_a_shell_by_interpolation() -> None:
    """Inputs pass through env only, so a crafted value cannot inject shell (zizmor)."""
    runs = [str(s.get("run", "")) for s in _job()["steps"]]

    assert [r for r in runs if "${{" in r] == []


def test_certify_failed_gate_does_not_fail_the_job() -> None:
    """Exit 1 is a failed gate, a valid outcome; every other non-zero exit fails the step."""
    script = str(_step("Certify (temporary store, nothing recorded)")["run"])

    assert "1) echo" in script
    assert '*) echo "::error::hermes certify exited $code' in script


def test_certify_judges_the_funnel_evidence() -> None:
    """v2's backtest gate includes the funnel (v2 §7), so certify gets the evidence file."""
    script = str(_step("Certify (temporary store, nothing recorded)")["run"])

    assert '--funnel-evidence "$out/funnel-evidence.json"' in script


def test_certify_writes_to_a_temporary_store() -> None:
    """A workflow run never records a certification; that stays on the VPS."""
    script = str(_step("Certify (temporary store, nothing recorded)")["run"])

    assert '--store-dir "$RUNNER_TEMP/cert"' in script


def test_artifact_is_kept_90_days_even_after_a_failure() -> None:
    """A trial logged before a later failure still leaves the run, so it can be committed."""
    upload = _step("Upload report, performance, evidence and trial lines")

    assert upload["with"]["retention-days"] == 90
    assert str(upload["if"]).startswith("${{ always()")


def _backtest_runner() -> dict[str, Any]:
    """Return the backtest-runner service from the runner compose file."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return dict(compose["services"]["backtest-runner"])


def test_backtest_runner_registers_the_label_the_workflow_asks_for() -> None:
    """The compose label and the workflow's runs-on agree, or no run is ever picked up."""
    labels = _backtest_runner()["environment"]["RUNNER_LABELS"].split(",")

    assert labels == ["self-hosted", "linux", "x64", "hermes-backtest"]


def test_backtest_runner_stays_inside_its_budget() -> None:
    """One replica, 4 CPUs and 8 GB, as ADR-023 §8 sets."""
    service = _backtest_runner()

    assert (service["scale"], service["cpus"], service["mem_limit"]) == (1, 4, "8g")


def test_backtest_runner_never_starts_with_the_pc_stack() -> None:
    """Behind its own profile, ./up.sh on the PC cannot start a backtest runner."""
    assert _backtest_runner()["profiles"] == ["backtest"]


def test_backtest_runner_reads_data_from_the_server_mount() -> None:
    """The data dir the driver reads is the P310 mount, at the same path inside."""
    service = _backtest_runner()

    assert "/srv/hermes-data:/srv/hermes-data" in service["volumes"]
    assert service["environment"]["HERMES_DATA_DIR"] == "/srv/hermes-data"


def test_backtest_runner_shares_no_cache_and_no_docker_socket() -> None:
    """Its volumes are its own (ADR-018 D11): no shared cache, no host Docker socket."""
    volumes = _backtest_runner()["volumes"]

    assert sorted(volumes) == [
        "/srv/hermes-data:/srv/hermes-data",
        "backtest-uv-cache:/home/runner/.cache/uv",
    ]


def _starter_check(tmp_path: Path, actor: str, triggering_actor: str) -> int:
    """Run the Validation starter step for the given run actors; return its exit code."""
    result = _run_step(
        "Code Owner started this Validation touch",
        {"OWNER": "ricardoafo", "ACTOR": actor, "TRIGGERING_ACTOR": triggering_actor},
        tmp_path,
    )
    return result.returncode


@needs_tools
def test_validation_started_by_the_owner_proceeds(tmp_path: Path) -> None:
    """The Code Owner pressing Run workflow himself is the Validation OK."""
    assert _starter_check(tmp_path, "ricardoafo", "ricardoafo") == 0


@needs_tools
@pytest.mark.parametrize(
    ("actor", "triggering_actor"),
    [
        ("rafo-claude-bot", "rafo-claude-bot"),
        ("someone-else", "someone-else"),
        ("ricardoafo", "rafo-claude-bot"),
        ("rafo-claude-bot", "ricardoafo"),
        ("RicardoAFO", "RicardoAFO"),
        ("", ""),
    ],
    ids=["bot", "other-user", "bot-re-run", "bot-started", "case-variant", "empty"],
)
def test_validation_started_by_anyone_else_fails_closed(
    tmp_path: Path, actor: str, triggering_actor: str
) -> None:
    """Any run not both started and (re-)triggered by the owner stops before data."""
    assert _starter_check(tmp_path, actor, triggering_actor) == 1


_HASH_STEP = "Signed pre-registration hash"
_FUNNEL_STEP = "Funnel (nothing recorded)"
_SIGNED_HASH = "212a109f39b1c31384803d94c842ec550181628883a708deab25eaf2a5535128"


def test_pre_registration_hash_is_a_required_string_input() -> None:
    """No run starts without the signed hash the funnel compares the file against."""
    field = _workflow()[True]["workflow_dispatch"]["inputs"]["pre_registration_sha256"]

    assert (field["type"], field["required"]) == ("string", True)


def test_pre_registration_hash_reaches_steps_only_through_env() -> None:
    """The typed hash is never spliced into a shell, so it cannot inject commands (zizmor)."""
    expression = "${{ inputs.pre_registration_sha256 }}"
    runs = [str(s.get("run", "")) for s in _job()["steps"]]

    assert [r for r in runs if "inputs.pre_registration_sha256" in r] == []
    assert _step(_HASH_STEP)["env"]["PRE_REG_SHA256"] == expression
    assert _step(_FUNNEL_STEP)["env"]["PRE_REG_SHA256"] == expression


def test_funnel_reads_the_signed_v2_pre_registration() -> None:
    """The funnel judges against the committed v2 document, the one whose hash is signed."""
    path = _workflow()["env"]["PRE_REGISTRATION"]

    assert path == "docs/experiments/swing-mvp1/pre-registration-v2.md"
    assert (_ROOT / path).is_file()


def test_hash_is_checked_first_and_unconditionally() -> None:
    """A bad hash stops the run before the trial-log copy, so no trial is ever logged."""
    names = [s.get("name") for s in _job()["steps"]]

    assert names.index(_HASH_STEP) == 0
    assert names.index(_HASH_STEP) < names.index("Copy the experiment trial log")
    assert "if" not in _step(_HASH_STEP)


def _hash_check(tmp_path: Path, value: str) -> subprocess.CompletedProcess[str]:
    """Run the hash step with ``value`` as the dispatched input."""
    return _run_step(_HASH_STEP, {"PRE_REG_SHA256": value}, tmp_path)


@needs_tools
@pytest.mark.parametrize("value", [_SIGNED_HASH, _SIGNED_HASH.upper()], ids=["lower", "upper"])
def test_a_64_hex_hash_proceeds(tmp_path: Path, value: str) -> None:
    """The signed hash, in either case, lets the run go on (the funnel ignores case)."""
    assert _hash_check(tmp_path, value).returncode == 0


@needs_tools
@pytest.mark.parametrize(
    "value",
    [
        "",
        _SIGNED_HASH[:-1],
        _SIGNED_HASH + "0",
        _SIGNED_HASH[:-1] + "g",
        _SIGNED_HASH + "\n",
        " " + _SIGNED_HASH,
        "$(touch pwned)" + _SIGNED_HASH[14:],
    ],
    ids=["empty", "63-chars", "65-chars", "non-hex", "trailing-newline", "leading-space", "shell"],
)
def test_a_malformed_hash_fails_with_an_error_annotation(tmp_path: Path, value: str) -> None:
    """Anything but exactly 64 hex characters stops the run with a visible ::error::."""
    result = _hash_check(tmp_path, value)

    assert result.returncode == 1
    assert result.stdout.startswith("::error::")
    assert not (tmp_path / "pwned").exists()


def test_backtest_writes_the_funnel_evidence() -> None:
    """The driver leaves the evidence file the funnel step reads, under out/ for the artifact."""
    script = str(_step("Backtest")["run"])

    assert '--funnel-evidence-out "$RUNNER_TEMP/out/funnel-evidence.json"' in script


def test_backtest_runs_the_pre_registered_strategy() -> None:
    """Every run replays the v2 roster entry, never the roster default (a stray v1 trial)."""
    script = str(_step("Backtest")["run"])

    assert _workflow()["env"]["STRATEGY"] == "swing-mvp1-v2"
    assert '--strategy "$STRATEGY"' in script


def test_funnel_runs_after_certify() -> None:
    """The funnel follows certify, so the v1 gate's outcome is reported unchanged."""
    names = [s.get("name") for s in _job()["steps"]]

    assert names.index("Certify (temporary store, nothing recorded)") < names.index(_FUNNEL_STEP)


_FAKE_UV = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_ARGS"
printf '%s\\n' "$FAKE_LINES"
exit "$FAKE_EXIT"
"""


def _funnel(tmp_path: Path, exit_code: int) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the funnel step against a fake ``uv`` that exits ``exit_code``; return result, temp."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text(_FAKE_UV)
    fake.chmod(0o755)
    runner_temp = tmp_path / "rt"
    (runner_temp / "out").mkdir(parents=True)
    (runner_temp / "out" / "report.json").write_text('{"strategy_id": "swing-mvp1-v2"}')
    (runner_temp / "github_output").write_text("")
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(runner_temp / "github_output"),
        "PRE_REGISTRATION": "docs/experiments/swing-mvp1/pre-registration-v2.md",
        "PRE_REG_SHA256": _SIGNED_HASH,
        "FAKE_ARGS": str(tmp_path / "args"),
        "FAKE_LINES": "FUNNEL PASSED",
        "FAKE_EXIT": str(exit_code),
    }
    return _run_step(_FUNNEL_STEP, env, tmp_path), runner_temp


@needs_tools
@pytest.mark.parametrize("exit_code", [0, 1], ids=["passed", "failed-or-refused"])
def test_funnel_verdict_does_not_fail_the_job(tmp_path: Path, exit_code: int) -> None:
    """Exit 0 and exit 1 are both valid outcomes; the step passes and records the code."""
    result, runner_temp = _funnel(tmp_path, exit_code)

    assert result.returncode == 0
    assert (runner_temp / "github_output").read_text() == f"exit={exit_code}\n"
    assert (runner_temp / "out" / "funnel.txt").read_text() == "FUNNEL PASSED\n"


@needs_tools
@pytest.mark.parametrize("exit_code", [2, 3, 127], ids=["usage", "file-error", "not-found"])
def test_funnel_error_exit_fails_the_step(tmp_path: Path, exit_code: int) -> None:
    """A usage, file or evidence error fails the step with its own exit code, still recorded."""
    result, runner_temp = _funnel(tmp_path, exit_code)

    assert result.returncode == exit_code
    assert f"::error::hermes funnel exited {exit_code}" in result.stdout
    assert (runner_temp / "github_output").read_text() == f"exit={exit_code}\n"


@needs_tools
def test_funnel_is_called_with_the_signed_hash_and_evidence(tmp_path: Path) -> None:
    """The command gets the dispatched hash and the driver's evidence, never a recomputed hash."""
    _, runner_temp = _funnel(tmp_path, 0)

    assert (tmp_path / "args").read_text().splitlines() == [
        "run",
        "hermes",
        "funnel",
        "swing-mvp1-v2",
        "--pre-registration",
        "docs/experiments/swing-mvp1/pre-registration-v2.md",
        "--evidence",
        f"{runner_temp}/out/funnel-evidence.json",
        "--expect-sha256",
        _SIGNED_HASH,
    ]


_FUNNEL_HEADER = (
    f"# hermes funnel swing-mvp1-v2: swing-mvp1-v2 (pre-registration sha256 {_SIGNED_HASH})"
)
_FUNNEL_REPORT = f"""{_FUNNEL_HEADER}
PASS  pbo                      observed 0.0137                 required <= 0.05
FAIL  t_stat                   observed 2.7181                 required > 3.0
PASS  trade_count              observed 4242                   required >= 100
FUNNEL FAILED
"""


@needs_tools
def test_summary_shows_funnel_verdicts_without_numbers(tmp_path: Path) -> None:
    """Only names and PASS/FAIL reach the job summary; observed metrics stay in the artifact."""
    runner_temp = tmp_path / "rt"
    (runner_temp / "out").mkdir(parents=True)
    (runner_temp / "out" / "funnel.txt").write_text(_FUNNEL_REPORT)
    summary = tmp_path / "summary.md"
    env = {
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_STEP_SUMMARY": str(summary),
        "EXPERIMENT": "swing-mvp1",
        "SPLIT": "train",
        "START": "2020-09-01",
        "END": "2024-06-30",
        "SHA": "ceaa676",
        "FUNNEL_EXIT": "1",
    }

    result = _run_step("Summary", env, tmp_path)
    text = summary.read_text()

    assert result.returncode == 0
    assert "- Funnel exit: 1\n" in text
    assert (
        f"```text\n{_FUNNEL_HEADER}\nPASS pbo\nFAIL t_stat\nPASS trade_count\nFUNNEL FAILED\n```\n"
    ) in text
    assert [n for n in ("0.0137", "2.7181", "4242", "0.05", "3.0") if n in text] == []


_CERTIFY_STEP = "Certify (temporary store, nothing recorded)"

_FAKE_UV_CERTIFY = """#!/usr/bin/env bash
if [ "$3" = "certify" ]; then
  printf '%s\\n' "GATE FAILED"
  exit "$FAKE_EXIT"
fi
printf '%s\\n' '{"count": 1}'
"""


def _certify(tmp_path: Path, exit_code: int) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the certify step against a fake ``uv`` whose certify exits ``exit_code``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text(_FAKE_UV_CERTIFY)
    fake.chmod(0o755)
    runner_temp = tmp_path / "rt"
    (runner_temp / "out").mkdir(parents=True)
    (runner_temp / "out" / "report.json").write_text(
        '{"strategy_id": "swing-mvp1-v2", "fills_count": 3}'
    )
    (runner_temp / "github_output").write_text("")
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(runner_temp / "github_output"),
        "FAKE_EXIT": str(exit_code),
    }
    return _run_step(_CERTIFY_STEP, env, tmp_path), runner_temp


@needs_tools
@pytest.mark.parametrize("exit_code", [0, 1], ids=["passed", "failed-or-refused"])
def test_certify_verdict_does_not_fail_the_job(tmp_path: Path, exit_code: int) -> None:
    """A gate verdict, passed or failed, keeps the step green under bash -e (issue #1036)."""
    result, runner_temp = _certify(tmp_path, exit_code)

    assert result.returncode == 0
    assert (runner_temp / "github_output").read_text() == f"exit={exit_code}\n"
    assert (runner_temp / "out" / "trial-stats.json").read_text() == '{"count": 1}\n'


@needs_tools
def test_certify_error_exit_fails_the_step(tmp_path: Path) -> None:
    """A usage, config, evidence or store error fails the step with its own exit code."""
    result, runner_temp = _certify(tmp_path, 3)

    assert result.returncode == 3
    assert "::error::hermes certify exited 3" in result.stdout
    assert (runner_temp / "github_output").read_text() == "exit=3\n"
