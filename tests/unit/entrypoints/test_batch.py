"""Tests for ``hermes batch check`` (issue #1057), on a real throwaway repository.

The repository holds a roster, two cards, two signal modules and a batch signed
the way pre-registration v2 was: a DRAFT precursor commit, then the signing
commit that records the precursor's hash. Every value is made up.
"""

import copy
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hermes.application.calibration.research_batch import BATCH_CHECK_NAMES
from hermes.entrypoints.batch import main
from hermes.entrypoints.commands import discover
from tests.support.research_batch import (
    BATCH_PATH,
    CARD_BYTES,
    batch_front_matter,
    batch_text,
    card_path,
)

_ROSTER = "strategies:\n  - name: swing-alpha\n  - name: swing-beta\n"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed git argv built by the test
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, bytes], message: str) -> str:
    for path, data in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _signed_repo(repo: Path, edit: dict[str, Any] | None = None) -> Path:
    """Commit code, then the DRAFT batch, then the signed batch; return the batch file."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    code = {f"src/hermes/{sid.replace('-', '_')}.py": b"code\n" for sid in CARD_BYTES}
    cards = {card_path(sid): data for sid, data in CARD_BYTES.items()}
    code_commit = _commit(
        repo, {**code, **cards, "config/strategies.yaml": _ROSTER.encode()}, "code"
    )
    fm = batch_front_matter()
    fm.update(edit or {})
    draft = copy.deepcopy(fm)
    draft.update(
        status="DRAFT",
        signed_by=None,
        date_signed=None,
        content_sha256=None,
        content_sha256_scope=None,
        signing_commit_precursor=None,
        code_commit=None,
    )
    draft_text = batch_text(draft)
    precursor = _commit(repo, {BATCH_PATH: draft_text.encode()}, "draft")
    fm.update(
        content_sha256=hashlib.sha256(draft_text.encode()).hexdigest(),
        content_sha256_scope=f"git show {precursor}:{BATCH_PATH} | sha256sum",
        signing_commit_precursor=precursor,
        code_commit=code_commit,
    )
    _commit(repo, {BATCH_PATH: batch_text(fm).encode()}, "sign")
    return repo / BATCH_PATH


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stream=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_a_signed_batch_passes_with_one_line_per_check(tmp_path: Path) -> None:
    """Exit 0, a header, one PASS line per check in contract order, then BATCH PASSED."""
    batch = _signed_repo(tmp_path)

    code, out, _ = _run("check", str(batch), "--repo", str(tmp_path), "--require-signed")

    lines = out.splitlines()
    assert (code, lines[0], [ln.split()[:2] for ln in lines[1:-1]], lines[-1]) == (
        0,
        f"# hermes batch check {BATCH_PATH}",
        [["PASS", name] for name in BATCH_CHECK_NAMES],
        "BATCH PASSED",
    )


def test_code_changed_after_binding_fails_with_exit_1(tmp_path: Path) -> None:
    """A change to a candidate's code after code_commit is a FAIL, not an error."""
    batch = _signed_repo(tmp_path)
    _commit(tmp_path, {"src/hermes/swing_alpha.py": b"edited\n"}, "edit")

    code, out, _ = _run("check", str(batch), "--repo", str(tmp_path))

    failed = [ln.split()[1] for ln in out.splitlines() if ln.startswith("FAIL")]
    assert (code, failed, out.splitlines()[-1]) == (1, ["code_paths"], "BATCH FAILED")


def test_a_hash_that_is_not_the_precursors_fails(tmp_path: Path) -> None:
    """A signed hash edited by hand no longer matches the precursor, so content_sha256 fails."""
    batch = _signed_repo(tmp_path)
    text = batch.read_text("utf-8")
    batch.write_text(re.sub(r"content_sha256: [0-9a-f]{64}", f"content_sha256: {'f' * 64}", text))

    code, out, _ = _run("check", str(batch), "--repo", str(tmp_path))

    failed = [ln.split()[1] for ln in out.splitlines() if ln.startswith("FAIL")]
    assert (code, failed) == (1, ["content_sha256"])


def test_a_batch_edited_after_signing_fails_the_signed_check(tmp_path: Path) -> None:
    """A commit that loosens a signed batch keeps its old hash, and --require-signed fails it."""
    batch = _signed_repo(tmp_path)
    edited = batch.read_text("utf-8").replace("fdr_q: 0.05", "fdr_q: 0.04")
    _commit(tmp_path, {BATCH_PATH: edited.encode()}, "edit after signing")

    code, out, _ = _run("check", str(batch), "--repo", str(tmp_path), "--require-signed")

    failed = [ln.split()[1] for ln in out.splitlines() if ln.startswith("FAIL")]
    assert (code, failed, out.splitlines()[-1]) == (1, ["content_sha256"], "BATCH FAILED")


def test_candidates_out_writes_the_workflow_matrix(tmp_path: Path) -> None:
    """Contract §1: batch id plus each candidate's strategy id and family, in run order."""
    batch = _signed_repo(tmp_path / "repo")
    out_file = tmp_path / "candidates.json"

    code, _, _ = _run(
        "check", str(batch), "--repo", str(tmp_path / "repo"), "--candidates-out", str(out_file)
    )

    assert (code, json.loads(out_file.read_text("utf-8"))) == (
        0,
        {
            "batch_id": "b1",
            "candidates": [
                {"strategy_id": "swing-alpha", "idea_family": "breakout"},
                {"strategy_id": "swing-beta", "idea_family": "pullback"},
            ],
        },
    )


def test_a_missing_file_exits_3(tmp_path: Path) -> None:
    """Contract: 3 is a file that cannot be read."""
    code, _, err = _run("check", str(tmp_path / "nope.md"), "--repo", str(tmp_path))

    assert (code, err.startswith("ERROR: batch file:")) == (3, True)


def test_a_file_outside_the_repository_exits_3(tmp_path: Path) -> None:
    """The batch is named by its path in the repository, so it must be inside it."""
    (tmp_path / "repo").mkdir()
    outside = tmp_path / "b1.md"
    outside.write_text(batch_text(), "utf-8")

    code, _, err = _run("check", str(outside), "--repo", str(tmp_path / "repo"))

    assert (code, "not inside the repository" in err) == (3, True)


def test_a_repository_without_a_roster_exits_3(tmp_path: Path) -> None:
    """strategy_ids_known needs config/strategies.yaml."""
    batch = tmp_path / BATCH_PATH
    batch.parent.mkdir(parents=True)
    batch.write_text(batch_text(), "utf-8")

    code, _, err = _run("check", str(batch), "--repo", str(tmp_path))

    assert (code, err.startswith("ERROR: roster:")) == (3, True)


@pytest.mark.parametrize("argv", [[], ["verify", "x.md"], ["check"], ["check", "x", "--bogus"]])
def test_usage_errors_exit_2(argv: list[str]) -> None:
    """Contract: 2 is usage."""
    assert _run(*argv)[0] == 2


def test_help_exits_0_and_names_the_flags() -> None:
    """--help prints the usage on stdout."""
    code, out, _ = _run("--help")

    assert (code, "--require-signed" in out, "--candidates-out" in out) == (0, True, True)


def test_the_command_is_registered_as_batch() -> None:
    """`hermes batch` is discovered like every other command."""
    assert "batch" in discover()
