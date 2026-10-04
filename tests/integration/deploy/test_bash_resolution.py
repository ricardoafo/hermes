"""Which bash the deploy tests run (#618).

Git for Windows' ``bin/bash.exe`` launcher puts ``/usr/bin`` in front of
``PATH``, so the real coreutils would shadow the stubs the fake box puts first.
The harness swaps it for the ``usr/bin/bash.exe`` it wraps. These tests use
fake Git layouts and run on every platform.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration.deploy._bash import resolve_bash


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_git_launcher_is_swapped_for_the_bash_it_wraps(tmp_path: Path) -> None:
    """``<Git>/bin/bash.exe`` resolves to ``<Git>/usr/bin/bash.exe``, which leaves PATH alone."""
    launcher = _touch(tmp_path / "Git" / "bin" / "bash.exe")
    wrapped = _touch(tmp_path / "Git" / "usr" / "bin" / "bash.exe")
    assert resolve_bash(str(launcher)) == str(wrapped)


def test_wrapped_bash_is_kept(tmp_path: Path) -> None:
    """``<Git>/usr/bin/bash.exe`` is already the real bash and is used as given."""
    wrapped = _touch(tmp_path / "Git" / "usr" / "bin" / "bash.exe")
    assert resolve_bash(str(wrapped)) == str(wrapped)


def test_launcher_without_a_wrapped_bash_is_kept(tmp_path: Path) -> None:
    """A ``bin/bash.exe`` with no ``usr/bin/bash.exe`` beside it is not Git's launcher."""
    launcher = _touch(tmp_path / "Other" / "bin" / "bash.exe")
    assert resolve_bash(str(launcher)) == str(launcher)


def test_posix_bash_is_kept() -> None:
    """A Linux path such as ``/bin/bash`` is used as given."""
    assert resolve_bash("/bin/bash") == "/bin/bash"


def test_unset_means_bash_on_path() -> None:
    """With ``HERMES_TEST_BASH`` unset or empty, the tests run ``bash`` from PATH."""
    assert (resolve_bash(None), resolve_bash("")) == ("bash", "bash")
