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

from tests.support.paths import REPO_ROOT

_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "backtest-batch.yml"

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
            "SWEEP": "false",
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
            "SWEEP": "false",
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
            "SWEEP": "false",
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
            "FILE": str(_batch_doc(tmp_path, "status: SIGNED\n")),
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
            "SWEEP": "false",
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


def _batch_doc(tmp_path: Path, front_matter: str) -> Path:
    """Write a batch document with ``front_matter`` and a body that also says ``status:``."""
    doc = tmp_path / "b1.md"
    doc.write_text(f"---\n{front_matter}---\n\n# B1\n\nstatus: DRAFT in prose\n", encoding="utf-8")
    return doc


def _stub_batch_check(directory: Path, exit_code: int) -> Path:
    """Put a ``uv`` on PATH that stands in for ``hermes batch check``; return its call marker."""
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "called"
    script = directory / "uv"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n"
        'while [ "$#" -gt 0 ]; do [ "$1" = --candidates-out ] && out="$2"; shift; done\n'
        'printf \'{"candidates": [{"strategy_id": "swing-b1-a", "idea_family": "f1"}]}\' > "$out"\n'
        'echo "PASS front_matter detail"\n'
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return marker


def _check_batch(tmp_path: Path, doc: Path, check_exit: int) -> tuple[int, list[str], bool]:
    """Run the Batch check step on ``doc``; return exit code, GITHUB_OUTPUT lines, uv was called."""
    output = tmp_path / "out"
    output.write_text("")
    summary = tmp_path / "summary"
    summary.write_text("")
    marker = _stub_batch_check(tmp_path / "bin", check_exit)
    result = _run(
        "check",
        "Batch check",
        {
            "FILE": str(doc),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(tmp_path),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )
    return result.returncode, output.read_text().splitlines(), marker.exists()


@needs_tools
@pytest.mark.parametrize("status", ["DRAFT", '"DRAFT"', "'DRAFT'"])
def test_a_draft_batch_ends_green_with_no_candidates_and_runs_no_check(
    tmp_path: Path, status: str
) -> None:
    """A draft is only the precursor of the signed hash: green, empty candidate list, no check."""
    doc = _batch_doc(tmp_path, f"status: {status}\nbatch_id: b1\n")

    code, lines, called = _check_batch(tmp_path, doc, check_exit=1)

    assert (code, lines, called) == (0, ["candidates=[]", "chunks=[]", "draft=true"], False)


@needs_tools
@pytest.mark.parametrize(
    "front_matter", ["status: SIGNED\n", "batch_id: b1\n", "status: draftish\n"]
)
def test_any_batch_that_is_not_a_draft_goes_through_the_signed_check(
    tmp_path: Path, front_matter: str
) -> None:
    """Only the exact status DRAFT skips the check; SIGNED, no status or a lookalike is checked."""
    doc = _batch_doc(tmp_path, front_matter)

    refused = _check_batch(tmp_path, doc, check_exit=1)
    accepted = _check_batch(tmp_path, doc, check_exit=0)

    assert (refused[0], refused[2], accepted[0], accepted[1][0], accepted[2]) == (
        1,
        True,
        0,
        "draft=false",
        True,
    )


def test_every_job_after_check_is_skipped_for_a_draft() -> None:
    """An empty matrix would fail the run, so Train is skipped and the rest follow from it."""
    jobs = _workflow()["jobs"]

    assert jobs["train"]["if"] == "${{ needs.check.outputs.draft != 'true' }}"
    assert "needs.train.result != 'skipped'" in jobs["results"]["if"]
    assert jobs["judge-train"]["needs"] == ["check", "train"]


# ---------------------------------------------------------------------------
# Chunked Train and one Validation candidate per family (ADR-031 §2, §3; #1310)
# ---------------------------------------------------------------------------


def _stub_sweep_check(directory: Path, candidates: dict[str, Any]) -> None:
    """Put a ``uv`` on PATH that stands in for ``hermes batch check`` writing ``candidates``."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "uv"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'while [ "$#" -gt 0 ]; do [ "$1" = --candidates-out ] && out="$2"; shift; done\n'
        f"printf '%s' '{json.dumps(candidates)}' > \"$out\"\n"
        'echo "PASS front_matter"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _check_outputs(tmp_path: Path, candidates: dict[str, Any]) -> dict[str, str]:
    """Run the Batch check step on a signed document; return its GITHUB_OUTPUT."""
    _stub_sweep_check(tmp_path / "bin", candidates)
    output = tmp_path / "out"
    output.write_text("")
    result = _run(
        "check",
        "Batch check",
        {
            "FILE": str(_batch_doc(tmp_path, "status: SIGNED\n")),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "RUNNER_TEMP": str(tmp_path),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


@needs_tools
def test_train_runs_in_chunks_of_twenty_in_batch_order(tmp_path: Path) -> None:
    """45 candidates make chunks of 20, 20 and 5; each names its first position for the chain."""
    ids = [f"fam@n={i}" for i in range(45)]
    candidates = {
        "kind": "sweep",
        "candidates": [{"strategy_id": s, "idea_family": "fam"} for s in ids],
    }

    chunks = json.loads(_check_outputs(tmp_path, candidates)["chunks"])

    assert chunks == [
        {"chunk": 0, "first": 0, "ids": ids[:20]},
        {"chunk": 1, "first": 20, "ids": ids[20:40]},
        {"chunk": 2, "first": 40, "ids": ids[40:]},
    ]


@needs_tools
@pytest.mark.parametrize(("kind", "expected"), [("sweep", "true"), (None, "false")])
def test_the_check_says_whether_the_batch_is_a_sweep(
    tmp_path: Path, kind: str | None, expected: str
) -> None:
    """Only a sweep sends one candidate per family and judges Validation on the ones sent."""
    candidates: dict[str, Any] = {"candidates": [{"strategy_id": "swing-b1-a"}]}
    if kind is not None:
        candidates["kind"] = kind

    assert _check_outputs(tmp_path, candidates)["sweep"] == expected


def test_train_jobs_are_chunks_of_the_check_output() -> None:
    """The Train matrix is the chunk list, one job at a time, so the chain stays linear."""
    train = _workflow()["jobs"]["train"]

    assert (train["strategy"]["matrix"], train["timeout-minutes"]) == (
        {"chunk": "${{ fromJSON(needs.check.outputs.chunks) }}"},
        120,
    )


def _stub_driver(directory: Path, failing: str | None) -> Path:
    """Put a ``uv`` on PATH that stands in for the driver; return the file it logs ids to.

    It appends each ``--candidate`` to the log, writes the report, and fails on ``failing``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    calls = directory / "calls"
    script = directory / "uv"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in --candidate) id="$2";; --output) report="$2";; esac; shift\n'
        "done\n"
        f'echo "$id" >> {calls}\n'
        f'[ "$id" = "{failing}" ] && exit 1\n'
        'echo \'{"fills_count": 3}\' > "$report"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return calls


def _train_chunk(tmp_path: Path, ids: list[str], failing: str | None) -> tuple[int, list[str]]:
    """Run the Train Backtest step on ``ids``; return its exit code and the candidates it ran."""
    calls = _stub_driver(tmp_path / "bin", failing)
    result = _run(
        "train",
        "Backtest",
        {
            "RUNNER_TEMP": str(tmp_path / "rt"),
            "CHUNK_IDS": json.dumps(ids),
            "BATCH_FILE": "b.md",
            "START": "2020-09-01",
            "END": "2024-06-30",
            "MANIFEST": "m.json",
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )
    ran = calls.read_text().split() if calls.exists() else []
    return result.returncode, ran


@needs_tools
def test_a_chunk_runs_its_candidates_in_order_and_keeps_only_the_three_evidence_files(
    tmp_path: Path,
) -> None:
    """Each candidate gets its folder with evidence.json; the report, which holds values, goes."""
    ids = ["fam@n=1", "fam@n=2"]

    code, ran = _train_chunk(tmp_path, ids, failing=None)

    folder = tmp_path / "rt" / "out" / ids[1]
    assert (code, ran, sorted(p.name for p in folder.iterdir())) == (0, ids, ["evidence.json"])


@needs_tools
def test_a_candidate_that_errors_stops_the_chunk(tmp_path: Path) -> None:
    """ADR-026 §2: the candidates after an error never run, and the job fails."""
    ids = ["fam@n=1", "fam@n=2", "fam@n=3"]

    assert _train_chunk(tmp_path, ids, failing=ids[1]) == (1, ids[:2])


def _stub_sweep_judge(directory: Path, winners: list[str], selection: dict[str, Any]) -> Path:
    """Put a ``uv`` on PATH for the Train judge and the budget; return its argument log.

    ``hermes funnel`` lists ``winners``; ``validation_budget.py`` writes ``selection``
    and prints one PASS/FAIL line per winner.
    """
    directory.mkdir(parents=True, exist_ok=True)
    calls = directory / "calls"
    listed = json.dumps({"candidates": winners})
    budget_lines = "\\n".join(
        f"{'PASS' if w in selection['sent'] else 'FAIL'} {w} validation_budget" for w in winners
    )
    script = directory / "uv"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> {calls}\n'
        'args=" $* "\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in --out) out="$2";; --sent-out) sent="$2";; esac; shift\n'
        "done\n"
        'if [[ "$args" == *validation_budget.py* ]]; then\n'
        f"  printf '%s' '{json.dumps(selection)}' > \"$sent\"\n"
        f"  printf '{budget_lines}\\n'\n"
        "  exit 0\n"
        "fi\n"
        'mkdir -p "$out/verdicts"\n'
        f"printf '%s' '{listed}' > \"$out/validation-candidates.json\"\n"
        f"echo 'CANDIDATES {' '.join(winners)}'\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return calls


@needs_tools
def test_a_sweep_sends_only_the_winners_with_budget_left_in_batch_order(tmp_path: Path) -> None:
    """ADR-031 §3: the Validation matrix is the selection's sent list, each with its position."""
    candidates = [
        {"strategy_id": s, "idea_family": s.split("@")[0], "index": i}
        for i, s in enumerate(["a@n=1", "a@n=2", "b@n=1", "c@n=1"])
    ]
    winners = ["a@n=2", "b@n=1", "c@n=1"]
    selection = {"winners": winners, "sent": ["a@n=2", "c@n=1"], "over_budget": ["b@n=1"]}
    _stub_sweep_judge(tmp_path / "bin", winners, selection)
    runner_temp = tmp_path / "rt"
    (runner_temp / "chain").mkdir(parents=True)
    output, summary = tmp_path / "output", tmp_path / "summary"
    output.write_text("")
    summary.write_text("")

    result = _run(
        "judge-train",
        "Judge the batch on Train",
        {
            "RUNNER_TEMP": str(runner_temp),
            "BATCH_FILE": "b.md",
            "BATCH_ID": "b1",
            "SWEEP": "true",
            "CANDIDATES": json.dumps(candidates),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(output),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert (result.returncode, json.loads(outputs["validation_candidates"])) == (
        0,
        [candidates[1], candidates[3]],
    )
    assert "FAIL b@n=1 validation_budget" in summary.read_text()


@needs_tools
@pytest.mark.parametrize(("sweep", "passed"), [("true", True), ("false", False)])
def test_a_sweeps_champion_is_judged_on_the_candidates_it_sent(
    tmp_path: Path, sweep: str, passed: bool
) -> None:
    """ADR-031 §4: --sent names Train's selection record for a sweep, and only for a sweep."""
    _stub_uv(tmp_path / "bin", "CHAMPION none", 1, {})
    calls = tmp_path / "bin" / "calls"
    uv = tmp_path / "bin" / "uv"
    uv.write_text(
        uv.read_text().replace(
            "#!/usr/bin/env bash\n", f'#!/usr/bin/env bash\necho "$*" >> {calls}\n', 1
        )
    )
    runner_temp = tmp_path / "rt"
    (runner_temp / "chain").mkdir(parents=True)

    result = _run(
        "judge-validation",
        "Judge the batch on Validation",
        {
            "RUNNER_TEMP": str(runner_temp),
            "BATCH_FILE": "b.md",
            "BATCH_ID": "b1",
            "SWEEP": sweep,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        tmp_path,
        path_prefix=tmp_path / "bin",
    )

    expected = f"--sent {runner_temp}/train/sweep-selection.json"
    assert (result.returncode, expected in calls.read_text()) == (0, passed)
