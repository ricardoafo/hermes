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
    """Run a step's shell with ``env`` on top of a minimal environment."""
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run(  # noqa: S603 — fixed interpreter, the workflow's own script
        ["bash", "-c", str(_step(name)["run"])],  # noqa: S607 — bash from PATH, as on the runner
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
    """A Validation run uses backtest-validation, whose reviewer click is the Validation OK."""
    assert _job()["environment"] == (
        "${{ inputs.split == 'validation' && 'backtest-validation' || 'backtest-train' }}"
    )


def test_approval_check_runs_before_any_data_is_read() -> None:
    """The run's own approval history is checked before data is filled or a trial logged."""
    names = [s.get("name") for s in _job()["steps"]]

    assert names.index("Code Owner approved this Validation touch") < names.index(
        "Fill and verify the pinned data"
    )
    assert _step("Code Owner approved this Validation touch")["if"] == (
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

    assert scopes == [{"contents": "read"}, {"contents": "read", "actions": "read"}]


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
