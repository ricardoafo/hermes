"""Release names and the package versions that carry them (ADR-022).

Releases are named in SemVer: ``X.Y.Z`` or ``X.Y.Z-shadow.N`` (ADR-006,
ADR-014). PEP 440 cannot hold ``-shadow.N``, so the package metadata stores
the shadow pre-release as alpha ``N``: release ``1.0.0-shadow.1`` is package
``1.0.0a1``. Both map one-to-one and order the same way.

This module uses only the standard library and imports nothing from hermes,
so the release scripts load it by path in CI jobs that have not installed the
package.
"""

from __future__ import annotations

import re

__all__ = ["changelog_section", "release_label", "to_package_version", "to_semver"]

_SEMVER_RE = re.compile(r"^(?P<core>\d+\.\d+\.\d+)(?:-shadow\.(?P<n>[1-9]\d*))?$")
_PACKAGE_RE = re.compile(r"^(?P<core>\d+\.\d+\.\d+)(?:a(?P<n>[1-9]\d*))?$")


def to_package_version(release: str) -> str:
    """Return the PEP 440 package version that carries ``release``.

    Raises:
        ValueError: ``release`` is not ``X.Y.Z`` or ``X.Y.Z-shadow.N``.
    """
    match = _SEMVER_RE.match(release)
    if match is None:
        raise ValueError(f"{release!r} is not X.Y.Z or X.Y.Z-shadow.N (ADR-022)")
    n = match.group("n")
    return match.group("core") + (f"a{n}" if n else "")


def to_semver(package_version: str) -> str:
    """Return the release name a package version carries.

    Raises:
        ValueError: ``package_version`` is not one :func:`to_package_version` makes.
    """
    match = _PACKAGE_RE.match(package_version)
    if match is None:
        raise ValueError(f"{package_version!r} is not a hermes package version")
    n = match.group("n")
    return match.group("core") + (f"-shadow.{n}" if n else "")


def release_label(package_version: str) -> str:
    """Return how screens name a package version: ``v1.0.0-shadow.1`` for ``1.0.0a1``.

    A version the cut never writes (a local or development build) is shown as
    it is, so a status screen never fails on it.
    """
    try:
        return f"v{to_semver(package_version)}"
    except ValueError:
        return package_version


def changelog_section(changelog: str, release: str) -> str | None:
    """Return the body of ``## [release]`` in a Keep-a-Changelog file, or None.

    The body runs to the next ``## `` heading or the trailing link references,
    with surrounding blank lines removed.
    """
    lines = changelog.splitlines()
    prefix = f"## [{release}]"
    start = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or re.match(r"^\[[^\]]+\]: \S+$", line):
            break
        body.append(line)
    return "\n".join(body).strip("\n") + "\n"
