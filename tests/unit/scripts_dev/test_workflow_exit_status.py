"""Workflow steps read a command's exit status without tripping ``-e`` (issue #1206).

GitHub runs every ``run:`` block with ``bash -e``. A step that writes

    some-command
    rc=$?

never reaches ``rc=$?`` when the command fails: the shell exits first, so the
step skips whatever it meant to do with the status (write the summary, file
the issue, accept a FAIL verdict). On 18 Oct this hid the failing fresh seeds.
The safe form is ``rc=0`` then ``some-command || rc=$?``, or a ``set +e``
around the command.
"""

import re

import yaml

from tests.support.paths import REPO_ROOT

_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
_BARE_STATUS = re.compile(r"^\s*\w+=(\$\?|\$\{PIPESTATUS\[\d+\]\})\s*$")

# Sites that may keep a bare exit-status read. None: the list only shrinks, and a
# new site fails the guard instead of joining it.
_KNOWN: set[str] = set()


def _run_blocks() -> list[tuple[str, str]]:
    """Return ``(where, script)`` for every ``run:`` block in every workflow."""
    blocks = []
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (workflow.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                if "run" in step:
                    name = step.get("name", f"step {index}")
                    blocks.append((f"{path.name}:{job_id}:{name}", str(step["run"])))
    return blocks


def bare_status_reads(script: str) -> list[str]:
    """Return the lines that read ``$?`` on their own while ``-e`` is still on."""
    found = []
    errexit = True
    for line in script.splitlines():
        if re.match(r"^\s*set\s+\+e\b", line):
            errexit = False
        elif re.match(r"^\s*set\s+-e\b", line):
            errexit = True
        elif errexit and _BARE_STATUS.match(line):
            found.append(line.strip())
    return found


def _offenders() -> set[str]:
    return {
        f"{where}: {line}" for where, script in _run_blocks() for line in bare_status_reads(script)
    }


def test_the_detector_flags_a_bare_status_read_under_errexit() -> None:
    """The check finds the 18 Oct pattern and accepts the two safe forms."""
    assert bare_status_reads("cmd > out\nrc=$?\n") == ["rc=$?"]
    assert bare_status_reads("cmd | tee f\ncode=${PIPESTATUS[0]}\n") == ["code=${PIPESTATUS[0]}"]
    assert bare_status_reads("rc=0\ncmd > out || rc=$?\n") == []
    assert bare_status_reads("set +e\ncmd\nrc=$?\nset -e\n") == []


def test_no_new_step_reads_an_exit_status_after_errexit_killed_it() -> None:
    """No workflow step outside the known list reads `$?` on its own line under `-e`."""
    assert _offenders() - _KNOWN == set()


def test_the_known_list_only_names_sites_that_still_exist() -> None:
    """A fixed site leaves the list, so the list can only shrink to empty."""
    assert _KNOWN - _offenders() == set()
