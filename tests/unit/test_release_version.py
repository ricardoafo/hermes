"""Unit and property tests for ``hermes.release_version`` (ADR-022 §1, §3).

Protects:
- Release names map one-to-one onto PEP 440 package versions, in SemVer order.
- Only ``X.Y.Z`` and ``X.Y.Z-shadow.N`` are release names.
- A release's changelog section is found exactly, without its neighbours.
"""

from __future__ import annotations

import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.version import Version

from hermes.release_version import changelog_section, to_package_version, to_semver

release_names = st.builds(
    lambda major, minor, patch, shadow: (
        f"{major}.{minor}.{patch}" + (f"-shadow.{shadow}" if shadow else "")
    ),
    st.integers(0, 30),
    st.integers(0, 30),
    st.integers(0, 30),
    st.one_of(st.none(), st.integers(1, 500)),
)

_CHANGELOG = """# Changelog

## [Unreleased]

## [1.0.0-shadow.2] - 2026-10-20

### Fixed

- Later fix.

## [1.0.0-shadow.1] - 2026-10-06

### Added

- First feature.

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
[1.0.0-shadow.1]: https://github.com/ricardoafo/hermes/releases/tag/v1.0.0-shadow.1
"""


@given(release_names)
def test_package_version_round_trips_to_the_release_name(release: str) -> None:
    """Every release name maps to a valid PEP 440 version and back unchanged."""
    package = to_package_version(release)

    assert str(Version(package)) == package
    assert to_semver(package) == release


@given(release_names, release_names)
def test_package_versions_order_like_semver(a: str, b: str) -> None:
    """Shadow N sorts before shadow N+1, and every shadow before its final release."""

    def semver_key(v: str) -> tuple[int, int, int, int]:
        core, _, shadow = v.partition("-shadow.")
        major, minor, patch = (int(x) for x in core.split("."))
        return (major, minor, patch, int(shadow) if shadow else sys.maxsize)

    pa, pb = Version(to_package_version(a)), Version(to_package_version(b))

    assert (pa < pb) == (semver_key(a) < semver_key(b))


def test_the_first_shadow_release_is_alpha_one() -> None:
    """ADR-022's first cut, 1.0.0-shadow.1, is package 1.0.0a1."""
    assert to_package_version("1.0.0-shadow.1") == "1.0.0a1"


@pytest.mark.parametrize(
    "bad", ["1.0", "v1.0.0", "1.0.0-rc.1", "1.0.0-shadow.0", "1.0.0-shadow", "1.0.0a1"]
)
def test_malformed_release_names_are_refused(bad: str) -> None:
    """Only X.Y.Z and X.Y.Z-shadow.N (N >= 1) are release names."""
    with pytest.raises(ValueError, match=r"X\.Y\.Z"):
        to_package_version(bad)


@pytest.mark.parametrize("bad", ["1.0.0-shadow.1", "1.0.0rc1", "1.0.0a0", "1.0.0.dev1"])
def test_foreign_package_versions_are_refused(bad: str) -> None:
    """A package version the cut never writes has no release name."""
    with pytest.raises(ValueError, match="not a hermes package version"):
        to_semver(bad)


def test_changelog_section_stops_at_the_next_release() -> None:
    """The newer release's section holds only its own entries."""
    assert changelog_section(_CHANGELOG, "1.0.0-shadow.2") == "### Fixed\n\n- Later fix.\n"


def test_changelog_section_stops_at_the_link_references() -> None:
    """The oldest release's section ends before the link references."""
    assert changelog_section(_CHANGELOG, "1.0.0-shadow.1") == "### Added\n\n- First feature.\n"


def test_changelog_section_is_none_for_an_uncut_version() -> None:
    """A version with no dated section, such as 0.0.0 or a future shadow, has no release notes."""
    sections = [changelog_section(_CHANGELOG, v) for v in ("0.0.0", "1.0.0-shadow.3")]

    assert sections == [None, None]
