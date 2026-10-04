"""Invariants for ``.github/workflows/cd.yml`` and its link to the bootstrap script.

The CD workflow has never run for real, and nothing else in CI parses it.
These tests pin the failures a first run would hit: an aggregator that can
never fail, a removed job that is still referenced, and real deploy steps
that would run during a dry run (issue #179).

Reference: openspec/changes/179-cd-first-real-deploy/change.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CD_PATH = _REPO_ROOT / ".github" / "workflows" / "cd.yml"
_BOOTSTRAP_PATH = _REPO_ROOT / "scripts" / "deploy" / "vps_bootstrap.sh"

_DRY_RUN_GUARD = "inputs.dry_run != true"
_DRY_RUN_NOTICE_GUARD = "inputs.dry_run == true"
# Steps that may run in a dry run: installing the ssh client touches only the runner.
_ALWAYS_RUN_DEPLOY_STEPS = {"Ensure openssh-client is installed"}


def _workflow() -> dict[str, Any]:
    """Parse cd.yml."""
    loaded = yaml.safe_load(_CD_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps(job: str) -> list[dict[str, Any]]:
    """Return the steps of one job."""
    return list(_workflow()["jobs"][job]["steps"])


def _label(step: dict[str, Any]) -> str:
    """Return a step's name, or its action for an unnamed `uses:` step."""
    return str(step.get("name", step.get("uses", "")))


def _dry_run_guarded(step: dict[str, Any]) -> bool:
    """Return whether a step's condition skips it on a dry run."""
    return _DRY_RUN_GUARD in str(step.get("if", ""))


def test_confirm_needs_every_other_job() -> None:
    """The aggregator must wait on every job, or a failure upstream goes unreported."""
    jobs = _workflow()["jobs"]

    assert sorted(jobs["confirm"]["needs"]) == sorted(set(jobs) - {"confirm"})


def test_health_check_job_is_gone() -> None:
    """The self-ping job always returned 200, so the SSH health gate replaced it (D-7)."""
    assert "health-check" not in _workflow()["jobs"]


def test_workflow_never_references_the_removed_healthcheck_secret() -> None:
    """No step may still read the healthchecks ping secret the removed job used."""
    assert "VPS_HEALTHCHECK_URL" not in _CD_PATH.read_text(encoding="utf-8")


def test_workflow_never_copies_the_wheel_to_a_non_wheel_filename() -> None:
    """A wheel renamed to hermes.whl is rejected by uv; the bundle keeps the original name."""
    stale = "/tmp/hermes.whl"  # noqa: S108 (searched for, never opened)
    assert stale not in _CD_PATH.read_text(encoding="utf-8")


def test_confirm_fails_through_contains_on_needs_results() -> None:
    """The aggregator reads `needs.*.result`; the old grep on '"result":"failure"' never matched."""
    confirm_steps = _steps("confirm")

    conditions = [str(step.get("if", "")) for step in confirm_steps]
    assert any(
        "contains(needs.*.result, 'failure')" in cond
        and "contains(needs.*.result, 'cancelled')" in cond
        for cond in conditions
    )


def test_confirm_runs_even_when_an_upstream_job_failed() -> None:
    """`if: !cancelled()` makes confirm run after a failed job, so it can report the failure."""
    assert _workflow()["jobs"]["confirm"]["if"] == "!cancelled()"


def test_every_real_deploy_step_is_skipped_on_dry_run() -> None:
    """A dry run must not join the tailnet, scp or ssh: each real step carries the guard."""
    unguarded = [
        _label(step)
        for step in _steps("deploy")
        if _label(step) not in _ALWAYS_RUN_DEPLOY_STEPS
        and not _dry_run_guarded(step)
        and step.get("if") != _DRY_RUN_NOTICE_GUARD
    ]

    assert unguarded == []


def test_deploy_ssh_and_scp_steps_are_all_guarded() -> None:
    """Invariant (A-1.3): every step that runs ssh or scp, or joins the tailnet, is guarded."""
    risky = [
        step
        for step in _steps("deploy")
        if _label(step) not in _ALWAYS_RUN_DEPLOY_STEPS
        and step.get("if") != _DRY_RUN_NOTICE_GUARD
        and (
            re.search(r"\b(ssh|scp)\b", step.get("run", "")) or "tailscale" in step.get("uses", "")
        )
    ]

    assert len(risky) >= 3
    assert [_label(step) for step in risky if not _dry_run_guarded(step)] == []


def test_bundle_job_runs_without_a_dry_run_guard() -> None:
    """Invariant (A-1.3): the bundle is built in a dry run too, so its steps carry no guard."""
    guarded = [step.get("name", step.get("uses")) for step in _steps("bundle") if "if" in step]

    assert guarded == []


def test_deploy_waits_for_the_bundle() -> None:
    """Ordering: the deploy job installs only a bundle that was built."""
    assert _workflow()["jobs"]["deploy"]["needs"] == "bundle"


def test_smoke_runs_after_the_install_and_before_the_rollback() -> None:
    """Smoke checks the release the install step just put live (#407).

    Smoke lives in the deploy job because only that runner has the tailnet
    and SSH key. It must come after the install, and the rollback that
    reacts to it must come after it.
    """
    labels = [_label(step) for step in _steps("deploy")]

    install = labels.index("Install release, restart hermes.service, run health gate")
    smoke = labels.index("Smoke tests")
    rollback = labels.index("Roll back after a failed smoke run")
    assert install < smoke < rollback


def test_smoke_step_fails_when_no_tests_run() -> None:
    """Exit code 5 (no tests collected) must fail the deploy, not pass it (#407).

    The old step mapped exit 5 to success, so every deploy was green with
    nothing checked. The step now runs pytest directly and requires the
    smoke environment, so a missing SSH target fails instead of skipping.
    """
    step = next(step for step in _steps("deploy") if step.get("name") == "Smoke tests")

    assert "uv run pytest tests/smoke -m smoke" in step["run"]
    assert "-eq 5" not in step["run"]
    assert step["env"]["HERMES_SMOKE_REQUIRED"] == "1"


def test_failed_smoke_rolls_back_only_after_a_smoke_failure() -> None:
    """The rollback step fires on a failed smoke run and never on a dry run."""
    step = next(
        step
        for step in _steps("deploy")
        if step.get("name") == "Roll back after a failed smoke run"
    )

    assert "steps.smoke.outcome == 'failure'" in step["if"]
    assert _dry_run_guarded(step)
    assert "/opt/hermes/bin/rollback.sh" in step["run"]


def _setup_uv_versions(job: str) -> list[str]:
    """Return the `astral-sh/setup-uv` versions used in one job."""
    return [
        str(step["with"]["version"])
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv")
    ]


def test_bootstrap_installs_the_same_uv_version_as_the_bundle_job() -> None:
    """The VPS uv and the CI uv that built the lock must be the same version."""
    match = re.search(r"^UV_VERSION=(\S+)$", _BOOTSTRAP_PATH.read_text(encoding="utf-8"), re.M)

    assert match is not None
    assert [match.group(1)] == _setup_uv_versions("bundle")


@pytest.mark.parametrize("job", ["bundle", "deploy"])
def test_setup_uv_versions_agree_across_jobs(job: str) -> None:
    """Every job that uses uv pins the same version as the reusable wheel build."""
    wheel_workflow = yaml.safe_load(
        (_REPO_ROOT / ".github" / "workflows" / "_build-wheel.yml").read_text(encoding="utf-8")
    )
    wheel_versions = [
        str(step["with"]["version"])
        for step in wheel_workflow["jobs"]["build"]["steps"]
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv")
    ]

    assert _setup_uv_versions(job) == wheel_versions


def test_bootstrap_python_pin_is_an_exact_patch_inside_the_supported_range() -> None:
    """The VPS interpreter is one exact 3.14.x, matching `requires-python = ">=3.14,<3.15"`."""
    match = re.search(r"^PYTHON_VERSION=(\S+)$", _BOOTSTRAP_PATH.read_text(encoding="utf-8"), re.M)
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert match is not None
    assert re.fullmatch(r"3\.14\.\d+", match.group(1))
    assert 'requires-python = ">=3.14,<3.15"' in pyproject


def test_every_script_a_unit_runs_from_current_deploy_is_in_the_bundle() -> None:
    """Units run /opt/hermes/current/deploy/<x>.sh, so the bundle job must ship each one."""
    units = (_REPO_ROOT / "deploy" / "systemd").glob("*.service")
    referenced = {
        name
        for unit in units
        for name in re.findall(
            r"/opt/hermes/current/deploy/([\w.-]+\.sh)", unit.read_text(encoding="utf-8")
        )
    }
    bundle_text = "\n".join(str(step.get("run", "")) for step in _steps("bundle"))

    assert referenced
    assert {name for name in referenced if f"deploy/{name}" not in bundle_text} == set()


def test_deploy_step_uses_the_command_the_sudoers_rule_allows() -> None:
    """The sudoers rule allows `/usr/bin/bash <stage>/bin/install_release.sh`; cd.yml runs that."""
    deploy_runs = "\n".join(str(step.get("run", "")) for step in _steps("deploy"))
    bootstrap = _BOOTSTRAP_PATH.read_text(encoding="utf-8")

    assert '"${as_root[@]}" /usr/bin/bash "$stage/bin/install_release.sh" "$sha12" "$stage"' in (
        deploy_runs
    )
    assert "/usr/bin/bash /var/tmp/hermes-release-$sha/bin/install_release.sh $sha" in bootstrap


_RELEASE_SHA_EXPR = "${{ github.event.workflow_run.head_sha || github.sha }}"
_SERVE_PATH_CONTRACTS = {
    "tests/contract/test_binance_public_klines.py",
    "tests/contract/test_telegram_getme.py",
    "tests/contract/test_healthchecks_ping.py",
    "tests/contract/test_sentry_dsn_parses.py",
}


def test_release_sha_is_the_commit_ci_validated() -> None:
    """An automatic run ships workflow_run.head_sha, not main's tip when CD starts (#404)."""
    assert _workflow()["env"]["RELEASE_SHA"] == _RELEASE_SHA_EXPR


def test_every_checkout_and_the_wheel_build_use_the_release_sha() -> None:
    """Every job that reads the tree reads the validated commit, so nothing newer slips in."""
    jobs = _workflow()["jobs"]
    checkouts = [
        step
        for job in jobs.values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert checkouts
    assert all(step["with"]["ref"] == "${{ env.RELEASE_SHA }}" for step in checkouts)
    assert jobs["build"]["with"]["ref"] == _RELEASE_SHA_EXPR


def test_no_step_names_the_release_from_github_sha() -> None:
    """GITHUB_SHA under workflow_run is the wrong commit; only RELEASE_SHA may name a release."""
    text = _CD_PATH.read_text(encoding="utf-8")
    code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]

    assert not [line for line in code_lines if "GITHUB_SHA" in line]
    assert [line for line in code_lines if "github.sha" in line] == [
        f"  RELEASE_SHA: {_RELEASE_SHA_EXPR}",
        f"      ref: {_RELEASE_SHA_EXPR}",
    ]


def test_gate_runs_e2e_and_serve_path_contracts_before_the_bar_hold() -> None:
    """Only a release that passed the slow tiers reaches build; the hold comes last."""
    labels = [_label(step) for step in _steps("gate")]
    runs = " ".join(str(step.get("run", "")) for step in _steps("gate"))

    assert labels.index("End-to-end serve cycle on the release commit") < labels.index(
        "Hold around the 4h bar close"
    )
    assert labels.index("Contract tests for the adapters serve uses") < labels.index(
        "Hold around the 4h bar close"
    )
    assert "tests/e2e -m e2e" in runs
    assert set(re.findall(r"tests/contract/\S+\.py", runs)) == _SERVE_PATH_CONTRACTS
    assert _workflow()["jobs"]["build"]["needs"] == "gate"
