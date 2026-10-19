"""Tests for finding the checkout and reading its signed pre-registration (issue #1186).

Invariants:

- The project root is the nearest directory at or above the start that holds
  ``pyproject.toml``; with none, as in an installed package, it is refused.
- ``load_pre_registration`` reads the signed file under the root it is given,
  and without one under the checkout this test runs from.
"""

from pathlib import Path

import pytest

from hermes.infrastructure.project_root import find_project_root, load_pre_registration
from tests.support.paths import REPO_ROOT


def test_the_root_is_the_nearest_directory_with_pyproject(tmp_path: Path) -> None:
    """A nested start walks up to the first ``pyproject.toml``, not past it."""
    outer = tmp_path / "outer"
    inner = outer / "inner"
    (inner / "src" / "pkg").mkdir(parents=True)
    (outer / "pyproject.toml").write_text("", encoding="utf-8")
    (inner / "pyproject.toml").write_text("", encoding="utf-8")

    assert find_project_root(inner / "src" / "pkg") == inner.resolve()


def test_no_pyproject_up_to_the_file_system_root_is_refused(tmp_path: Path) -> None:
    """An installed package has no checkout to find, so the caller must name the root."""
    with pytest.raises(FileNotFoundError, match=r"no pyproject\.toml"):
        find_project_root(tmp_path)


def test_by_default_the_checkout_under_test_is_found() -> None:
    """From inside the repository the walk lands on its root."""
    assert find_project_root() == REPO_ROOT.resolve()


def test_the_pre_registration_is_read_under_the_named_root() -> None:
    """The named root and the default one read the same signed file."""
    expected = REPO_ROOT / "docs" / "experiments" / "swing-mvp1" / "pre-registration-v1.md"

    assert (
        load_pre_registration(REPO_ROOT).pre_reg_path(),
        load_pre_registration().pre_reg_path().resolve(),
    ) == (expected, expected.resolve())


def test_a_root_without_the_signed_file_is_refused(tmp_path: Path) -> None:
    """A root that holds no pre-registration fails at load, never later."""
    with pytest.raises(FileNotFoundError, match="Pre-registration file not found"):
        load_pre_registration(tmp_path)
