"""Invariants for ``.github/workflows/backtest-batch.yml`` (ADR-026 §2, §3, issue #1060).

The workflow cannot run outside server-01, so these tests pin what a first
batch would otherwise discover: who may start it, which batch file it reads,
the one-at-a-time order, the Validation guards, and that nothing but
PASS/FAIL lines reaches the job summary. Steps with plain shell run against
temporary files, with a stub in place of ``uv run hermes``.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github" / "workflows" / "backtest-batch.yml"

needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None or shutil.which("git") is None,
    reason="the steps' shell needs bash, jq and git, which the runner image has",
)


def _workflow() -> dict[Any, Any]:
    """Parse backtest-batch.yml."""
    loaded = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _step(job: str, name: str) -> dict[str, Any]:
    """Return one step of a job by name."""
    return next(s for s in _workflow()["jobs"][job]["steps"] if s.get("name") == name)


def _run(
    job: str, name: str, env: dict[str, str], cwd: Path, path_prefix: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a step's shell with ``env`` on top of a minimal environment.

    ``-e`` matches the runner's default ``bash -e {0}``: without it a non-zero exit in the middle of
    a step goes unnoticed here and kills the step in CI.
    """
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if path_prefix is not None:
        path = f"{path_prefix}:{path}"
    return subprocess.run(  # noqa: S603 — fixed interpreter, the workflow's own script
        ["bash", "-e", "-c", str(_step(job, name)["run"])],  # noqa: S607 — bash from PATH, as on the runner
        env={"PATH": path, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_batches_start_only_from_a_push_to_main_on_batch_paths() -> None:
    """The automatic trigger is a push to main on batch documents and nothing else."""
    on = _workflow()[True]

    assert on == {"push": {"branches": ["main"], "paths": ["docs/experiments/*/batches/**"]}}


def test_batch_check_refuses_an_unsigned_batch_and_writes_the_candidate_list() -> None:
    """The check job passes --require-signed, so a batch that is not signed starts no run."""
    run = _step("check", "Batch check")["run"]
    assert "hermes batch check" in run
    assert "--require-signed" in run
    assert "--candidates-out" in run


def test_batch_runs_share_the_backtest_concurrency_group_and_queue() -> None:
    """Never two backtests at once with backtest.yml, and a pending run is not cancelled."""
    assert _workflow()["concurrency"] == {"group": "backtest", "cancel-in-progress": False}


@pytest.mark.parametrize("job", ["train", "validation"])
def test_candidates_run_one_after_another(job: str) -> None:
    """The trial log chain is linear, so a candidate matrix never runs two jobs at once."""
    strategy = _workflow()["jobs"][job]["strategy"]

    assert (strategy["max-parallel"], strategy["fail-fast"]) == (1, True)


def test_jobs_run_in_the_adr_026_order() -> None:
    """Check, Train, Train judge, Validation, champion, results: each waits on the ones before."""
    jobs = _workflow()["jobs"]

    assert {name: job.get("needs") for name, job in jobs.items()} == {
        "check": None,
        "train": "check",
        "judge-train": ["check", "train"],
        "validation": ["check", "judge-train"],
        "judge-validation": ["check", "judge-train", "validation"],
        "results": ["check", "train", "judge-train", "validation", "judge-validation"],
    }


def test_only_the_results_job_reads_a_secret() -> None:
    """The backtest jobs keep contents: read and no secret; the results App key is the only one."""
    jobs = _workflow()["jobs"]
    workflow = _workflow()

    holders = {
        name
        for name, job in jobs.items()
        if "secrets." in yaml.safe_dump(job) or "github.token" in yaml.safe_dump(job)
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in jobs.values())
    assert holders == {"results"}
    assert "github.token" not in _WORKFLOW.read_text(encoding="utf-8")


def test_results_job_uses_its_own_environment_and_a_scoped_app_token() -> None:
    """ADR-026 §6: a dedicated App with contents and pull requests only, in its own environment."""
    job = _workflow()["jobs"]["results"]
    mint = next(s for s in job["steps"] if s.get("name") == "Mint the results App token")

    assert job["environment"] == "backtest-results"
    assert mint["with"] == {
        "app-id": "${{ vars.HERMES_RESULTS_APP_ID }}",
        "private-key": "${{ secrets.HERMES_RESULTS_APP_KEY }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }


def test_results_branch_prefix_is_fixed_and_the_checkout_keeps_no_credentials() -> None:
    """The App may only push below batch-results/, and no step persists a token in .git."""
    job = _workflow()["jobs"]["results"]
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]

    assert job["env"]["BRANCH_PREFIX"] == "batch-results/"
    push = next(s for s in job["steps"] if s.get("name") == "Commit and push the results branch")
    assert 'branch="${BRANCH_PREFIX}${BATCH_ID}-${RUN_ID}"' in push["run"]
    assert all(c["with"]["persist-credentials"] is False for c in checkouts)


@needs_tools
def test_results_job_requires_the_trial_log_chain_whenever_verdicts_exist() -> None:
    """A failed chain download must stop the job, not give a verdicts-only PR."""
    run = _step("results", "Assemble the results")["run"]
    assert "--require-chain" in run


def test_results_pr_metadata_failing_does_not_fail_the_run_after_the_pr_is_open() -> None:
    """A renamed label or a closed milestone leaves a warning, not a red run with an open PR."""
    script = _step("results", "Open the PR")["with"]["script"]
    opened = script.index("pulls.create")
    guarded = script.index("try {")

    assert opened < script.index("issues.update")
    assert guarded < script.index("issues.update")
    assert "core.warning" in script[guarded:]


def test_results_job_is_skipped_with_a_notice_until_the_app_is_configured(tmp_path: Path) -> None:
    """Without HERMES_RESULTS_APP_ID the job succeeds, opens nothing and says where lines stay."""
    output = tmp_path / "out"
    output.write_text("")
    env = {"BATCH_ID": "b1", "EXPERIMENT": "swing-mvp1", "GITHUB_OUTPUT": str(output)}

    result = _run("results", "Results App configured", {**env, "APP_ID": ""}, tmp_path)

    assert (result.returncode, output.read_text()) == (0, "configured=false\n")
    assert "::notice::" in result.stdout


@needs_tools
@pytest.mark.parametrize("name", ["B1", "b1/../x", "b1 x", ""])
def test_results_job_refuses_a_batch_id_that_is_not_a_plain_name(tmp_path: Path, name: str) -> None:
    """The branch name is built from the batch id, so anything but [a-z0-9-] stops the job."""
    output = tmp_path / "out"
    output.write_text("")
    env = {
        "BATCH_ID": name,
        "EXPERIMENT": "swing-mvp1",
        "GITHUB_OUTPUT": str(output),
        "APP_ID": "1",
    }

    result = _run("results", "Results App configured", env, tmp_path)

    assert result.returncode == 1


def test_results_job_runs_after_a_failed_batch_but_not_a_refused_one() -> None:
    """Trial lines already appended still count, so a failed Train still gets its results PR."""
    condition = _workflow()["jobs"]["results"]["if"]

    assert "!cancelled()" in condition
    assert "needs.check.result == 'success'" in condition
    assert "needs.train.result != 'skipped'" in condition


def test_validation_runs_in_the_main_only_validation_environment() -> None:
    """Validation and its champion pick use the environment that accepts only main."""
    jobs = _workflow()["jobs"]

    assert (jobs["validation"]["environment"], jobs["judge-validation"]["environment"]) == (
        "backtest-validation",
        "backtest-validation",
    )
    assert jobs["train"]["environment"] == "backtest-train"


def test_validation_is_skipped_when_no_candidate_passed_train() -> None:
    """A batch whose candidates all fail Train spends no Validation touch."""
    assert _workflow()["jobs"]["validation"]["if"] == (
        "${{ needs.judge-train.outputs.validation_count != '0' }}"
    )


@needs_tools
def test_only_the_code_owner_may_start_a_batch(tmp_path: Path) -> None:
    """A bot push starts nothing: the actor must be the repository owner (ADR-026 §2)."""
    env = {"OWNER": "ricardoafo", "REF": "refs/heads/main"}

    owner = _run("check", "Code Owner merged this", {**env, "ACTOR": "ricardoafo"}, tmp_path)
    bot = _run("check", "Code Owner merged this", {**env, "ACTOR": "rafo-claude-bot"}, tmp_path)

    assert (owner.returncode, bot.returncode) == (0, 1)


@needs_tools
def test_a_branch_never_starts_a_batch(tmp_path: Path) -> None:
    """Only reviewed code on main runs on server-01, even for the owner's push."""
    result = _run(
        "check",
        "Code Owner merged this",
        {"OWNER": "ricardoafo", "ACTOR": "ricardoafo", "REF": "refs/heads/feature"},
        tmp_path,
    )

    assert result.returncode == 1


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with a fixed identity and return its stdout."""
    identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
    return subprocess.run(  # noqa: S603 — fixed git arguments in a temporary repository
        ["git", *identity, "-C", str(repo), *args],  # noqa: S607 — git from PATH, as on the runner
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _repo_with_changes(tmp_path: Path, changed: list[str]) -> tuple[str, str]:
    """Commit an empty tree, then a commit adding ``changed``; return both SHAs."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    before = _git(tmp_path, "rev-parse", "HEAD")
    for name in changed:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "change")
    return before, _git(tmp_path, "rev-parse", "HEAD")


@needs_tools
def test_the_changed_batch_is_named_from_the_push(tmp_path: Path) -> None:
    """The run reads the one batch the push added, with its experiment and id."""
    before, after = _repo_with_changes(tmp_path, ["docs/experiments/swing-mvp1/batches/b1.md"])
    output = tmp_path / "out"
    output.write_text("")

    result = _run(
        "check",
        "The one batch this push changed",
        {"BEFORE": before, "SHA": after, "GITHUB_OUTPUT": str(output)},
        tmp_path,
    )

    assert (result.returncode, output.read_text().splitlines()) == (
        0,
        [
            "file=docs/experiments/swing-mvp1/batches/b1.md",
            "batch_id=b1",
            "experiment=swing-mvp1",
        ],
    )


@needs_tools
@pytest.mark.parametrize(
    "changed",
    [
        [],
        [
            "docs/experiments/swing-mvp1/batches/b1.md",
            "docs/experiments/swing-mvp1/batches/b2.md",
        ],
    ],
)
def test_zero_or_several_changed_batches_start_nothing(tmp_path: Path, changed: list[str]) -> None:
    """One push runs one batch; anything else is ambiguous about which hash was signed."""
    before, after = _repo_with_changes(tmp_path, changed)
    output = tmp_path / "out"
    output.write_text("")

    result = _run(
        "check",
        "The one batch this push changed",
        {"BEFORE": before, "SHA": after, "GITHUB_OUTPUT": str(output)},
        tmp_path,
    )

    assert (result.returncode, output.read_text()) == (1, "")


@needs_tools
def test_a_validation_rerun_is_refused(tmp_path: Path) -> None:
    """A second attempt would be a second touch, so only the first attempt runs (ADR-026 §3)."""
    first = _run("validation", "First attempt of a batch run", {"ATTEMPT": "1"}, tmp_path)
    again = _run("validation", "First attempt of a batch run", {"ATTEMPT": "2"}, tmp_path)

    assert (first.returncode, again.returncode) == (0, 1)


def _stub_uv(directory: Path, stdout: str, exit_code: int, files: dict[str, str]) -> None:
    """Put a ``uv`` on PATH that stands in for ``hermes funnel --batch``.

    It writes ``files`` under the ``--out`` directory, prints ``stdout`` and exits ``exit_code``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    writes = "\n".join(
        f"printf '%s' '{content}' > \"$out/{name}\"" for name, content in files.items()
    )
    script = directory / "uv"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'while [ "$#" -gt 0 ]; do [ "$1" = --out ] && out="$2"; shift; done\n'
        'mkdir -p "$out/verdicts"\n'
        f"{writes}\n"
        f"cat <<'EOF'\n{stdout}\nEOF\n"
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


@needs_tools
def test_train_judge_keeps_only_pass_fail_in_the_summary_and_lists_who_goes_on(
    tmp_path: Path,
) -> None:
    """The summary shows PASS/FAIL only, and the Validation matrix keeps batch order."""
    stdout = (
        "PASS swing-b1-a dsr\nFAIL swing-b1-b pbo\nsharpe 1.87 secret detail\nCANDIDATES swing-b1-a"
    )
    _stub_uv(
        tmp_path / "bin",
        stdout,
        0,
        {"validation-candidates.json": '{"candidates": ["swing-b1-a"]}'},
    )
    runner_temp = tmp_path / "rt"
    (runner_temp / "evidence-by-artifact" / "evidence-train-swing-b1-a").mkdir(parents=True)
    (runner_temp / "chain").mkdir()
    (runner_temp / "chain" / "trial-log.jsonl").write_text("")
    summary, output = tmp_path / "summary", tmp_path / "output"
    summary.write_text("")
    output.write_text("")
    candidates = [
        {"strategy_id": "swing-b1-a", "idea_family": "fa", "index": 0},
        {"strategy_id": "swing-b1-b", "idea_family": "fb", "index": 1},
    ]

    result = _run(
        "judge-train",
        "Judge the batch on Train",
        {
            "RUNNER_TEMP": str(runner_temp),
            "BATCH_FILE": "docs/experiments/swing-mvp1/batches/b1.md",
            "BATCH_ID": "b1",
            "CANDIDATES": json.dumps(candidates),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(output),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert result.returncode == 0
    assert "1.87" not in summary.read_text()
    assert "secret detail" not in summary.read_text()
    assert "PASS swing-b1-a dsr" in summary.read_text()
    assert outputs["validation_count"] == "1"
    assert json.loads(outputs["validation_candidates"]) == [candidates[0]]


@needs_tools
def test_train_judge_with_no_candidate_going_on_is_not_a_failure(tmp_path: Path) -> None:
    """Exit 1 from the judge means nobody goes on, which is a verdict and sets the count to 0."""
    _stub_uv(
        tmp_path / "bin",
        "FAIL swing-b1-a dsr\nCANDIDATES",
        1,
        {"validation-candidates.json": '{"candidates": []}'},
    )
    runner_temp = tmp_path / "rt"
    (runner_temp / "chain").mkdir(parents=True)
    (runner_temp / "chain" / "trial-log.jsonl").write_text("")
    output = tmp_path / "output"
    output.write_text("")
    summary = tmp_path / "summary"
    summary.write_text("")

    result = _run(
        "judge-train",
        "Judge the batch on Train",
        {
            "RUNNER_TEMP": str(runner_temp),
            "BATCH_FILE": "b.md",
            "BATCH_ID": "b1",
            "CANDIDATES": '[{"strategy_id": "swing-b1-a", "idea_family": "fa", "index": 0}]',
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(output),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    assert (result.returncode, "validation_count=0" in output.read_text()) == (0, True)


@needs_tools
@pytest.mark.parametrize("code", [2, 3])
def test_a_judge_error_fails_the_job(tmp_path: Path, code: int) -> None:
    """Usage and data errors (exit 2 or 3) are failed steps, never read as 'nobody goes on'."""
    _stub_uv(tmp_path / "bin", "", code, {})
    runner_temp = tmp_path / "rt"
    (runner_temp / "chain").mkdir(parents=True)
    output = tmp_path / "output"
    output.write_text("")

    result = _run(
        "judge-train",
        "Judge the batch on Train",
        {
            "RUNNER_TEMP": str(runner_temp),
            "BATCH_FILE": "b.md",
            "BATCH_ID": "b1",
            "CANDIDATES": "[]",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "GITHUB_OUTPUT": str(output),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    assert result.returncode == code


@needs_tools
def test_batch_check_writes_its_summary_before_it_fails_the_step(tmp_path: Path) -> None:
    """A failing batch check still writes its summary, then fails the step."""
    (tmp_path / "bin").mkdir()
    uv = tmp_path / "bin" / "uv"
    uv.write_text("#!/usr/bin/env bash\necho 'FAIL swing-b1-a signature'\nexit 3\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    summary = tmp_path / "summary"
    summary.write_text("")

    result = _run(
        "check",
        "Batch check",
        {
            "RUNNER_TEMP": str(tmp_path / "rt"),
            "FILE": "b.md",
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    assert (result.returncode, summary.read_text(), "exited 3" in result.stdout) == (
        1,
        "FAIL swing-b1-a\n",
        True,
    )


@needs_tools
def test_validation_judge_accepts_a_fail_verdict_and_reports_it(tmp_path: Path) -> None:
    """Exit 1 from the Validation judge is a verdict: the summary is written and the step passes."""
    _stub_uv(
        tmp_path / "bin",
        "FAIL swing-b1-a dsr\nCHAMPION none",
        1,
        {},
    )
    runner_temp = tmp_path / "rt"
    (runner_temp / "chain").mkdir(parents=True)
    (runner_temp / "chain" / "trial-log.jsonl").write_text("")
    summary = tmp_path / "summary"
    summary.write_text("")

    result = _run(
        "judge-validation",
        "Judge the batch on Validation",
        {
            "RUNNER_TEMP": str(runner_temp),
            "BATCH_FILE": "b.md",
            "BATCH_ID": "b1",
            "CANDIDATES": "[]",
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    assert (result.returncode, "FAIL swing-b1-a dsr" in summary.read_text()) == (0, True)


@needs_tools
def test_an_exhausted_family_budget_names_the_candidate_before_failing_the_step(
    tmp_path: Path,
) -> None:
    """Budget exit 1 is reported with the candidate's name, not lost to the step's ``-e``."""
    (tmp_path / "bin").mkdir()
    uv = tmp_path / "bin" / "uv"
    uv.write_text("#!/usr/bin/env bash\necho 'family fa: 5 of 5 touches used'\nexit 1\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    summary = tmp_path / "summary"
    summary.write_text("")

    result = _run(
        "validation",
        "Family Validation budget",
        {
            "RUNNER_TEMP": str(tmp_path / "rt"),
            "BATCH_FILE": "b.md",
            "CANDIDATE": "swing-b1-a",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    assert (
        result.returncode,
        "swing-b1-a's family has no Validation touch left" in result.stdout,
    ) == (
        1,
        True,
    )
