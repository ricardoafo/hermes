"""Reads the Lab screen's committed experiment files from a clone (issue #1056).

Only files under ``docs/experiments/<experiment>/`` are read, and only the
ones :class:`~hermes.application.console.lab.LabFiles` names. Nothing is
written. A file that cannot be read is left out, so a half-checked-out tree
shows what it has.

Reference: openspec/changes/1056-lab-screen/change.md.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Final

from hermes.application.console.lab import LabFiles, LabSnapshot, build_lab

__all__ = ["EXPERIMENTS_REL", "FileLabReader", "find_experiments_root"]

EXPERIMENTS_REL: Final = Path("docs/experiments")
_SKIP: Final = frozenset({"templates"})


def find_experiments_root(start: Path) -> Path | None:
    """Return the first ``docs/experiments`` folder at or above ``start``, or ``None``."""
    here = start.resolve()
    for folder in (here, *here.parents):
        candidate = folder / EXPERIMENTS_REL
        if candidate.is_dir():
            return candidate
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _texts(folder: Path, pattern: str, repo: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(folder.glob(pattern)):
        text = _read(path) if path.is_file() else None
        if text is not None:
            out[path.relative_to(repo).as_posix()] = text
    return out


class FileLabReader:
    """The Lab's reader over a clone's ``docs/experiments`` folder.

    Args:
        root: The ``docs/experiments`` folder, or ``None`` when this machine
            has no clone of the experiments.
        values: Called on every read: may the trial log's figures be kept?
            The caller answers with
            :func:`~hermes.application.console.lab.values_allowed`.
    """

    def __init__(self, root: Path | None, *, values: Callable[[], bool]) -> None:
        """Remember where to read and whether figures may be kept."""
        self._root = root
        self._values = values

    def read(self) -> LabSnapshot | None:
        """Return the Lab's strategies, or ``None`` when no experiment is readable."""
        if self._root is None or not self._root.is_dir():
            return None
        repo = self._root.parent.parent
        experiments: list[LabFiles] = []
        for folder in sorted(p for p in self._root.iterdir() if p.is_dir()):
            if folder.name in _SKIP:
                continue
            notebook = folder / "lab-notebook.md"
            experiments.append(
                LabFiles(
                    experiment=folder.name,
                    batches=_texts(folder, "batches/*.md", repo),
                    pre_registrations=_texts(folder, "pre-registration-v*.md", repo),
                    cards=_texts(folder, "cards/*.md", repo),
                    trial_log=_read(folder / "trial-log.jsonl") or "",
                    verdicts=_texts(folder, "verdicts/*/*.json", repo),
                    notebook=notebook.relative_to(repo).as_posix() if notebook.is_file() else None,
                )
            )
        if not experiments:
            return None
        return build_lab(experiments, include_values=self._values())
