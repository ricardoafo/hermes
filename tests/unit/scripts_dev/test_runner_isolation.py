"""The runner pools are isolated from the code they run.

The CI pool (label ``hermes``) runs pull request code. It must not reach the
host (Docker socket, sudo), the registration PAT, or anything the ops runner
reads later (cache volumes): the ops runner holds the deploy key and the
merge credentials.
"""

import re
from typing import Any

import yaml

from tests.support.paths import REPO_ROOT

_RUNNER_DIR = REPO_ROOT / "scripts" / "dev" / "gh-runner"
_STAGE = re.compile(r"^FROM\s+\S+\s+AS\s+(\w+)\s*$", re.MULTILINE | re.IGNORECASE)


def _compose() -> dict[str, Any]:
    loaded = yaml.safe_load((_RUNNER_DIR / "compose.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _stages() -> dict[str, str]:
    """Return each Dockerfile stage's name and text, in file order."""
    text = (_RUNNER_DIR / "Dockerfile").read_text(encoding="utf-8")
    marks = list(_STAGE.finditer(text))
    return {
        m.group(1): text[m.start() : marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        for i, m in enumerate(marks)
    }


def _cache_volumes(service: dict[str, Any]) -> set[str]:
    named = (v for v in service.get("volumes", []) if isinstance(v, str) and not v.startswith("/"))
    return {v.split(":", 1)[0] for v in named}


def test_only_the_ops_stage_grants_sudo_and_ci_is_the_default_target() -> None:
    """A plain `docker build` gives the no-sudo image; sudoers lives in the ops stage alone."""
    stages = _stages()

    assert list(stages)[-1] == "ci"
    assert [name for name, text in stages.items() if "sudoers" in text] == ["ops"]
    assert "usermod -aG sudo" not in "".join(stages.values())


def test_every_service_but_the_ops_runner_uses_the_ci_image() -> None:
    """The pools that run pull request, review, backtest and Dependabot jobs have no sudo."""
    services = _compose()["services"]

    images = {name: s["image"] for name, s in services.items() if name.endswith("runner")}

    assert images.pop("ops-runner") == "gh-runner:ops"
    assert services["ops-runner"]["build"]["target"] == "ops"
    assert services["runner"]["build"]["target"] == "ci"
    assert set(images.values()) == {"gh-runner:latest"}


def test_no_service_mounts_the_host_docker_socket() -> None:
    """The host socket is root on the host."""
    text = (_RUNNER_DIR / "compose.yml").read_text(encoding="utf-8")
    volumes = [str(v) for s in _compose()["services"].values() for v in s.get("volumes", [])]

    assert [v for v in volumes if "docker.sock" in v] == []
    assert "/var/run/docker.sock:" not in text


def test_the_ops_and_review_runners_share_no_cache_with_the_ci_pool() -> None:
    """A cache the CI pool writes must not be read by the jobs that hold credentials."""
    services = _compose()["services"]
    pool = _cache_volumes(services["runner"])

    assert _cache_volumes(services["ops-runner"]) & pool == set()
    assert _cache_volumes(services["review-runner"]) & pool == set()
    assert (
        _cache_volumes(services["ops-runner"]) & _cache_volumes(services["review-runner"]) == set()
    )


def test_the_entrypoint_drops_the_pat_and_old_ssh_keys_before_the_runner_starts() -> None:
    """A job runs as the runner user, so the PAT must be gone from the environment it inherits."""
    text = (_RUNNER_DIR / "entrypoint.sh").read_text(encoding="utf-8")

    unset = text.index("unset HERMES_RUNNER_REGISTRATION_PAT RUNNER_TOKEN")
    run = text.index("exec ./run.sh")
    assert unset < run
    assert "HERMES_RUNNER_REGISTRATION_PAT" not in text[unset + 60 :]
    assert text.index("rm -rf /home/runner/.ssh") < run
