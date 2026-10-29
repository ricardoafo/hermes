#!/usr/bin/env python3
"""Cut a release: set the version, compile the changelog, write the PR body.

ADR-022 §2: a version is cut by a normal release PR, never by a workflow.
Run from a fresh branch off ``main``:

    uv run python scripts/dev/cut-release.py 0.2.0
    uv run python scripts/dev/cut-release.py --next --engine-change no

``--next`` picks the version a merge-window cut takes (ADR-028): PATCH when
every changelog entry is ``Fixed`` or ``Security`` and the engine line is
``no``, MINOR otherwise.

It sets ``version`` in ``pyproject.toml`` and ``__version__`` in
``src/hermes/__init__.py``, refreshes ``uv.lock`` with ``uv lock``, turns the
``[Unreleased]`` block plus every ``changelog.d/`` fragment into a dated
``[<version>]`` section under a fresh empty ``[Unreleased]``, deletes the
compiled fragments, and writes the release PR body outside the checkout
(``--body-out``, default ``<tmp>/hermes-release-<version>.md``).
The author fills in its ``Engine change:`` line (ADR-022 §7) before opening
the PR; ``pr-body-lint.sh`` rejects it otherwise.

Versions are ``0.Y.Z`` until a champion trades real money (ADR-027): MINOR
for features and any engine change, PATCH for fixes that change no engine
decision, so ``--engine-change yes`` on a PATCH bump is refused. The step out
of ``0.y.z`` is exactly ``1.0.0`` and needs ``--real-capital``. The
``X.Y.Z-shadow.N`` line is retired: no new one is cut, and the last one
(package ``1.0.0a1``) counts as ``0.1.0``.

Exit codes: 0 success (or dry run), 2 refused (bad, retired or non-increasing
version, engine change on a PATCH bump, 1.0.0 without --real-capital,
mismatched version files, malformed fragment, missing changelog).
"""

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from packaging.version import Version

from hermes.release_version import to_package_version

PYPROJECT_VERSION_RE = re.compile(r'^version = "(?P<v>[^"]+)"$', re.MULTILINE)
INIT_VERSION_RE = re.compile(r'^__version__ = "(?P<v>[^"]+)"$', re.MULTILINE)
UNRELEASED_HEADER = "## [Unreleased]"
LINK_REF_RE = re.compile(r"^\[[^\]]+\]: \S+$")
REPO_URL = "https://github.com/ricardoafo/hermes"
RETIRED_SHADOW_AS = "0.1.0"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_aggregator() -> ModuleType:
    """Import ``aggregate-changelog.py``; its hyphenated name blocks ``import``."""
    path = Path(__file__).resolve().parent / "aggregate-changelog.py"
    spec = importlib.util.spec_from_file_location("aggregate_changelog", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("aggregate_changelog", module)
    spec.loader.exec_module(module)
    return module


aggregate_changelog = _load_aggregator()


def read_current_version(pyproject_text: str, init_text: str) -> str:
    """Return the package version both version files agree on.

    Raises:
        ValueError: a file has no version line, or the two disagree.
    """
    in_pyproject = PYPROJECT_VERSION_RE.search(pyproject_text)
    in_init = INIT_VERSION_RE.search(init_text)
    if in_pyproject is None or in_init is None:
        raise ValueError("pyproject.toml or src/hermes/__init__.py has no version line")
    if in_pyproject.group("v") != in_init.group("v"):
        raise ValueError(
            f"pyproject.toml says {in_pyproject.group('v')!r} but "
            f"__version__ says {in_init.group('v')!r}"
        )
    return in_pyproject.group("v")


def check_release_name(release: str) -> None:
    """Refuse a name from the retired ``-shadow.N`` line (ADR-027 §1).

    Raises:
        ValueError: ``release`` is a ``-shadow.N`` pre-release.
    """
    if "-shadow." in release:
        raise ValueError(f"{release} uses the retired -shadow.N line; cut 0.Y.Z instead (ADR-027)")


def effective_version(package_version: str) -> Version:
    """Return where ``package_version`` sits in the ``0.y.z`` order.

    Only the retired shadow line made pre-releases, so any pre-release counts
    as ``RETIRED_SHADOW_AS`` (ADR-027 §2).
    """
    version = Version(package_version)
    return Version(RETIRED_SHADOW_AS) if version.is_prerelease else version


def check_newer(current_package: str, new_package: str) -> None:
    """Refuse a version that does not move forward.

    Raises:
        ValueError: ``new_package`` is not greater than ``current_package``.
    """
    if Version(new_package) <= effective_version(current_package):
        raise ValueError(f"{new_package} is not greater than the current {current_package}")


def check_bump(
    current_package: str, new_package: str, *, engine_change: str | None, real_capital: bool
) -> None:
    """Refuse a bump ADR-027 rules out.

    Raises:
        ValueError: an engine change on a PATCH bump (§1), or a step out of
            ``0.y.z`` that is not ``1.0.0`` or lacks ``real_capital`` (§3).
    """
    current, new = effective_version(current_package), Version(new_package)
    if engine_change == "yes" and new.release[:2] == current.release[:2]:
        raise ValueError(
            f"{new_package} is a PATCH bump; an engine change needs a MINOR cut (ADR-027 §1)"
        )
    if current.major == 0 and new.major >= 1:
        if new != Version("1.0.0"):
            raise ValueError(f"{new_package} skips 1.0.0; 0.y.z steps to 1.0.0 (ADR-027 §3)")
        if not real_capital:
            raise ValueError(
                f"{new_package} leaves 0.y.z; pass --real-capital only for the first release "
                "a champion trades real money with (ADR-027 §3)"
            )


PATCH_SECTIONS = frozenset({"Fixed", "Security"})


def next_version(current_package: str, sections: set[str], *, engine_change: str) -> str:
    """Return the release a cut at a merge window takes (ADR-028).

    PATCH when every entry is a fix or a security fix and the engine line is
    ``no``; MINOR otherwise (ADR-027 §1). From the retired shadow line the
    count starts at ``0.1.0``.

    Raises:
        ValueError: there is nothing to release.
    """
    if not sections:
        raise ValueError("no changelog entries since the last cut: nothing to release")
    major, minor, patch = effective_version(current_package).release[:3]
    if engine_change == "no" and sections <= PATCH_SECTIONS:
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor + 1}.0"


def entry_sections(changelog: str, fragments: dict[str, list[tuple[int, str]]]) -> set[str]:
    """Return the sections with at least one entry, hand-written or fragment."""
    _, legacy_body, _ = _split_unreleased(changelog)
    legacy = {name for name, lines in _sections(legacy_body).items() if "".join(lines).strip()}
    return legacy | {name for name, entries in fragments.items() if entries}


def set_version(text: str, pattern: re.Pattern[str], new: str) -> str:
    """Replace the first version line ``pattern`` matches with ``new``."""
    return pattern.sub(lambda m: m.group(0).replace(m.group("v"), new), text, count=1)


def _split_unreleased(changelog: str) -> tuple[str, str, str]:
    """Split the changelog into (before, unreleased body, after).

    The body runs from the line after the header to the next ``## [`` heading
    or, failing that, to the trailing link references.

    Raises:
        ValueError: there is no ``## [Unreleased]`` heading.
    """
    lines = changelog.splitlines()
    try:
        start = lines.index(UNRELEASED_HEADER)
    except ValueError:
        raise ValueError("CHANGELOG.md has no '## [Unreleased]' section") from None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
        len(lines),
    )
    if end == len(lines):
        while end > start + 1 and (not lines[end - 1].strip() or LINK_REF_RE.match(lines[end - 1])):
            end -= 1
    before = "\n".join(lines[:start])
    body = "\n".join(lines[start + 1 : end])
    after = "\n".join(lines[end:])
    return before, body, after


def _sections(body: str) -> dict[str, list[str]]:
    """Group a block's text by its ``### `` headings, in order of appearance."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in body.splitlines():
        if line.startswith("### "):
            current = sections.setdefault(line[4:].strip(), [])
        elif current is not None:
            current.append(line)
    return sections


def render_section(
    version: str,
    date: str,
    legacy_body: str,
    fragments: dict[str, list[tuple[int, str]]],
) -> str:
    """Render ``## [version] - date``: hand-written entries first, then fragments.

    Keep-a-Changelog sections come in their standard order; any other legacy
    heading (such as ``Notes``) follows them unchanged.
    """
    legacy = _sections(legacy_body)
    order = list(aggregate_changelog.SECTIONS) + [
        s for s in legacy if s not in aggregate_changelog.SECTIONS
    ]
    out = [f"## [{version}] - {date}", ""]
    for name in order:
        entries = [
            "\n".join(legacy.get(name, [])).strip(),
            *(body.rstrip() for _, body in fragments.get(name, [])),
        ]
        entries = [e for e in entries if e]
        if not entries:
            continue
        out += [f"### {name}", "", "\n\n".join(entries), ""]
    return "\n".join(out).rstrip() + "\n"


def cut_changelog(
    changelog: str,
    version: str,
    date: str,
    fragments: dict[str, list[tuple[int, str]]],
) -> tuple[str, str]:
    """Return (the new changelog, the new version's section).

    The new section sits under an empty ``[Unreleased]``, and a link
    reference for the version's tag joins the existing ones.
    """
    before, legacy_body, after = _split_unreleased(changelog)
    section = render_section(version, date, legacy_body, fragments)
    link = f"[{version}]: {REPO_URL}/releases/tag/v{version}"
    after_lines = after.splitlines()
    insert_at = next(
        (i + 1 for i, line in enumerate(after_lines) if line.startswith("[Unreleased]: ")),
        len(after_lines),
    )
    after_lines.insert(insert_at, link)
    parts = [before.rstrip(), "", UNRELEASED_HEADER, "", section.rstrip(), ""]
    tail = "\n".join(after_lines).strip("\n")
    if tail:
        parts += [tail, ""]
    return "\n".join(parts), section


def compiled_fragments(dir_path: Path) -> list[Path]:
    """Return the fragments the aggregator compiles."""
    if not dir_path.is_dir():
        return []
    return sorted(
        p
        for p in dir_path.iterdir()
        if p.is_file()
        and (
            aggregate_changelog.FRAGMENT_RE.match(p.name)
            or aggregate_changelog.ORPHAN_RE.match(p.name)
        )
    )


def render_pr_body(version: str, section: str, engine_change: str | None) -> str:
    """Render the release PR body (ADR-022 §2 and §7)."""
    engine = engine_change or "<yes or no: does this release change engine decisions? (ADR-021)>"
    notes = "\n".join(
        line.replace("### ", "#### ", 1) if line.startswith("### ") else line
        for line in section.splitlines()[2:]
    ).strip()
    return f"""## Summary

Release `v{version}` (ADR-022, ADR-027). Package metadata: `{to_package_version(version)}`.

## Release

Engine change: {engine}

## Related

- ADR: ADR-022, ADR-027
- Issue: N/A (release cut)

## Testing

Test tiers touched: none (release cut: version files, `uv.lock` and changelog only)

## Risk

Deploys `v{version}` to hermes-01 once merged and green.

## Changelog

{notes}
"""


def _run_uv_lock(root: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv is not on PATH; it refreshes uv.lock with the new version")
    subprocess.run([uv, "lock"], cwd=root, check=True)  # noqa: S603 — fixed argv; uv resolved via shutil.which


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and cut the release."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("version", nargs="?", help="0.Y.Z, or X.Y.Z from 1.0.0 on")
    target.add_argument(
        "--next",
        action="store_true",
        help="Pick the version from the changelog entries (ADR-028); needs --engine-change.",
    )
    parser.add_argument(
        "--date", default=None, help="Release date, YYYY-MM-DD (default: today, UTC)."
    )
    parser.add_argument("--engine-change", choices=("yes", "no"), default=None)
    parser.add_argument(
        "--real-capital",
        action="store_true",
        help="Allow the first 1.x cut: a champion trades real money with it (ADR-027 §3).",
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="Repository root (default: this checkout)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the new section; change nothing."
    )
    parser.add_argument("--no-lock", action="store_true", help="Skip `uv lock` (tests only).")
    parser.add_argument(
        "--body-out",
        type=Path,
        default=None,
        help="Where to write the PR body (default: <tmp>/hermes-release-<version>.md).",
    )
    args = parser.parse_args(argv)
    if args.next and args.engine_change is None:
        parser.error("--next needs --engine-change yes or no")

    root = args.root or _repo_root()
    pyproject = root / "pyproject.toml"
    init = root / "src" / "hermes" / "__init__.py"
    changelog = root / "CHANGELOG.md"
    frag_dir = root / "changelog.d"
    date = args.date or datetime.now(UTC).date().isoformat()

    try:
        pyproject_text = pyproject.read_text(encoding="utf-8")
        init_text = init.read_text(encoding="utf-8")
        changelog_text = changelog.read_text(encoding="utf-8")
        current_package = read_current_version(pyproject_text, init_text)
        fragments = aggregate_changelog.collect_fragments(frag_dir)
        version = (
            next_version(
                current_package,
                entry_sections(changelog_text, fragments),
                engine_change=args.engine_change,
            )
            if args.next
            else args.version
        )
        check_release_name(version)
        new_package = to_package_version(version)
        check_newer(current_package, new_package)
        check_bump(
            current_package,
            new_package,
            engine_change=args.engine_change,
            real_capital=args.real_capital,
        )
        new_changelog, section = cut_changelog(changelog_text, version, date, fragments)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(section, end="")
        return 0

    pyproject.write_text(
        set_version(pyproject_text, PYPROJECT_VERSION_RE, new_package), encoding="utf-8"
    )
    init.write_text(set_version(init_text, INIT_VERSION_RE, new_package), encoding="utf-8")
    changelog.write_text(new_changelog, encoding="utf-8")
    removed = compiled_fragments(frag_dir)
    for path in removed:
        path.unlink()
    if not args.no_lock:
        _run_uv_lock(root)
    body_out = args.body_out or Path(tempfile.gettempdir()) / f"hermes-release-{version}.md"
    body_out.write_text(render_pr_body(version, section, args.engine_change), encoding="utf-8")
    print(
        f"Cut v{version} ({new_package}): compiled {len(removed)} fragment(s); "
        f"PR body in {body_out}. Fill in its Engine change line before opening the PR.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
