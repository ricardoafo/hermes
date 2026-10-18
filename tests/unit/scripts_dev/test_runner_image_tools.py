"""The runner image carries every CLI the workflows call (issue #1206).

Every job runs on the server-01 runners, built from
``scripts/dev/gh-runner/Dockerfile``. GitHub-hosted images ship ``gh``; this
one did not, so the Sunday branch prune stopped at its first ``gh`` call on
18 Oct. A tool a workflow step or a script it calls needs must be installed
in the image, pinned and checksum-verified like gitleaks.
"""

import re

import yaml

from tests.support.paths import REPO_ROOT

_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
_DOCKERFILE = REPO_ROOT / "scripts" / "dev" / "gh-runner" / "Dockerfile"
_SCRIPT_CALL = re.compile(r"scripts/[\w./-]+\.(?:sh|py)")
_GH_CALL = re.compile(
    r"(?<![\w./-])gh\s+(?:api|auth|issue|label|pr|release|repo|run|secret|variable|workflow)\b"
    r"|command -v gh\b"
)


def _run_blocks() -> list[str]:
    blocks = []
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            blocks += [str(s["run"]) for s in job.get("steps") or [] if "run" in s]
    return blocks


def callers_of_gh() -> set[str]:
    """Return the workflows' run blocks and called scripts that invoke ``gh``."""
    found = set()
    for index, block in enumerate(_run_blocks()):
        if _GH_CALL.search(block):
            found.add(f"run block {index}")
        for script in _SCRIPT_CALL.findall(block):
            path = REPO_ROOT / script
            if path.is_file() and _GH_CALL.search(path.read_text(encoding="utf-8")):
                found.add(script)
    return found


def run_instructions(dockerfile: str) -> list[str]:
    """Return each live ``RUN`` instruction, continuation lines joined, comments dropped."""
    joined = re.sub(
        r"\\\n",
        " ",
        "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")),
    )
    return [line for line in joined.splitlines() if line.startswith("RUN ")]


def _gh_install() -> str:
    """Return the one RUN instruction that installs gh."""
    runs = [
        r
        for r in run_instructions(_DOCKERFILE.read_text(encoding="utf-8"))
        if "/usr/local/bin/gh" in r
    ]
    assert len(runs) == 1
    return runs[0]


def test_run_instructions_skip_comments_and_join_continuations() -> None:
    """A commented-out install is not an install; a continued RUN is one instruction."""
    text = "# RUN curl x \\\n#  && gh --version\nRUN a \\\n && b\nUSER x\n"
    assert run_instructions(text) == ["RUN a   && b"]


def test_the_scan_finds_the_jobs_that_need_gh() -> None:
    """The scan sees the branch prune script, so an empty result cannot pass by accident."""
    assert "scripts/dev/prune-merged-branches.sh" in callers_of_gh()


def test_the_image_installs_gh_when_a_job_calls_it() -> None:
    """A job that calls gh runs on an image whose live RUN puts gh on PATH and runs it."""
    assert callers_of_gh()
    install = _gh_install()
    assert "install -m 0755" in install
    assert install.rstrip().endswith("&& gh --version")


def test_gh_is_pinned_and_checksum_verified() -> None:
    """The gh binary comes from a pinned release whose SHA-256 the build checks, like gitleaks."""
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ARG GH_VERSION=\d+\.\d+\.\d+$", dockerfile, re.MULTILINE)
    assert re.search(r"^ARG GH_SHA256=[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert 'echo "${GH_SHA256}  /tmp/gh.tar.gz" | sha256sum -c -' in _gh_install()
