"""Guard: every docs folder is indexed, and living docs point at things that exist (issue #1197).

The repo audit found folder indexes that had drifted from their folders (a
guide listed that was gone, research and experiment files missing, no ADR
index at all) and living docs that linked to moved files or named source
paths that no longer existed. Nothing checked either, so each fix decayed.

Invariants:

- each indexed folder's ``README.md`` links every file and subfolder in it
  (for ``docs/experiments``, also every entry of its experiment and template
  folders), so a new document cannot land unlisted;
- the ADR index is exactly what ``scripts/dev/regenerate-adr-index.py``
  writes, so a new, retitled or superseded ADR updates it in the same PR;
- in living docs (``docs/`` minus the dated ``research``, ``decisions`` and
  ``experiments`` records, plus the root ``*.md`` files but the changelog), a
  relative Markdown link resolves to a file or folder in the repo;
- in living docs but the ADRs (frozen once accepted), a repo path written in
  inline code (``src/...``, ``tests/...``, ``scripts/...``, ``docs/...`` and
  the other top-level folders) exists.

Code blocks are skipped: they hold examples and templates, not references.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

_INDEXED: dict[str, tuple[str, ...]] = {
    "docs/architecture": (),
    "docs/design": (),
    "docs/experiments": ("swing-mvp1", "templates"),
    "docs/guides": (),
    "docs/research": (),
    "docs/research/paper-trading": (),
    "docs/runbook": (),
}
_DATED = ("docs/research", "docs/decisions", "docs/experiments")
# docs/guides/release.md is rewritten by PR #1205; it joins the check when that lands.
_NOT_YET_CHECKED = frozenset({"docs/guides/release.md"})

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"`[^`\n]+`")
_LINK = re.compile(r"\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
_PATH = re.compile(
    r"`((?:src|tests|scripts|docs|config|deploy|openspec|changelog\.d|\.github)/[^`\s]*)`"
)
_PLACEHOLDER = re.compile(r"[*<>{}$\[\]|]|YYYY|NNN|XX|\bvN\b")


@pytest.fixture(scope="module")
def root(pytestconfig: pytest.Config) -> Path:
    """Return the repository root, where ``pyproject.toml`` lives."""
    return pytestconfig.rootpath


def _link_targets(readme: Path) -> set[Path]:
    text = _INLINE.sub("", _FENCE.sub("", readme.read_text(encoding="utf-8")))
    return {
        (readme.parent / target.split("#")[0]).resolve()
        for target in _LINK.findall(text)
        if not re.match(r"^[a-z]+:", target) and not target.startswith("#")
    }


def _unlisted(root: Path, folder: str, deeper: tuple[str, ...]) -> set[str]:
    base = root / folder
    targets = _link_targets(base / "README.md")
    entries = [e for e in base.iterdir() if e.name != "README.md"]
    entries += [e for sub in deeper for e in (base / sub).iterdir()]
    return {
        str(entry.relative_to(root))
        for entry in entries
        if not any(t == entry.resolve() or t.is_relative_to(entry.resolve()) for t in targets)
    }


@pytest.mark.parametrize("folder", sorted(_INDEXED))
def test_each_index_links_every_entry_of_its_folder(root: Path, folder: str) -> None:
    """Every file and subfolder of an indexed folder is linked from its README."""
    assert _unlisted(root, folder, _INDEXED[folder]) == set()


def _load_adr_index(root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "regenerate_adr_index", root / "scripts" / "dev" / "regenerate-adr-index.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["regenerate_adr_index"] = module
    spec.loader.exec_module(module)
    return module


def test_the_adr_index_is_what_the_generator_writes(root: Path) -> None:
    """docs/architecture/README.md matches a fresh run of regenerate-adr-index.py."""
    generator = _load_adr_index(root)
    readme = (root / "docs" / "architecture" / "README.md").read_text(encoding="utf-8")

    assert generator.main(["--check"]) == 0, "run scripts/dev/regenerate-adr-index.py"
    assert generator.render(readme, generator.read_adrs()) == readme


def _living_docs(root: Path) -> list[Path]:
    docs = [
        path
        for path in sorted((root / "docs").rglob("*.md"))
        if not any(path.is_relative_to(root / dated) for dated in _DATED)
    ]
    docs += [path for path in sorted(root.glob("*.md")) if path.name != "CHANGELOG.md"]
    return [path for path in docs if str(path.relative_to(root)) not in _NOT_YET_CHECKED]


def _prose(path: Path) -> str:
    return _FENCE.sub("", path.read_text(encoding="utf-8"))


def _broken_links(root: Path, path: Path) -> list[str]:
    broken = []
    for target in _LINK.findall(_INLINE.sub("", _prose(path))):
        relative = target.split("#")[0]
        if re.match(r"^[a-z]+:", target) or not relative:
            continue
        resolved = (path.parent / relative).resolve()
        if resolved.is_relative_to(root) and not resolved.exists():
            broken.append(f"{path.relative_to(root)}: ({target})")
    return broken


def _missing_paths(root: Path, path: Path) -> list[str]:
    missing = []
    for written in _PATH.findall(_prose(path)):
        candidate = written.rstrip(".,:;").split(":")[0].split("#")[0]
        names_a_path = candidate.endswith("/") or "." in Path(candidate).name
        if _PLACEHOLDER.search(candidate) or not names_a_path:
            continue
        if not (root / candidate).exists():
            missing.append(f"{path.relative_to(root)}: `{written}`")
    return missing


def test_living_docs_link_only_to_things_that_exist(root: Path) -> None:
    """Every relative Markdown link in a living doc resolves inside the repo."""
    assert [bad for doc in _living_docs(root) for bad in _broken_links(root, doc)] == []


def test_living_docs_name_only_repo_paths_that_exist(root: Path) -> None:
    """Every repo path in inline code in a living doc but an ADR exists."""
    adrs = root / "docs" / "architecture"
    docs = [doc for doc in _living_docs(root) if not doc.is_relative_to(adrs)]

    assert [bad for doc in docs for bad in _missing_paths(root, doc)] == []


def test_the_link_check_catches_a_broken_link_and_skips_code(tmp_path: Path) -> None:
    """A missing target is reported; links in code blocks, URLs and anchors are not."""
    (tmp_path / "there.md").write_text("# There\n", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text(
        "[ok](there.md) [gone](gone.md#part) [web](https://example.com) [here](#top)\n"
        "`[code](inline.md)`\n```\n[fenced](fenced.md)\n```\n",
        encoding="utf-8",
    )

    assert _broken_links(tmp_path, doc) == ["doc.md: (gone.md#part)"]


def test_the_path_check_catches_a_missing_path_and_skips_placeholders(tmp_path: Path) -> None:
    """A missing repo path is reported; placeholders and bare words are not."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text(
        "`src/real.py:12` `src/gone.py` `docs/adr-NNN-x.md` `src/<name>.py` `tests/unit`\n",
        encoding="utf-8",
    )

    assert _missing_paths(tmp_path, doc) == ["doc.md: `src/gone.py`"]
