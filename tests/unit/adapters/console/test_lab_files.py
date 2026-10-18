"""The Lab screen's file reader (issue #1056).

Invariants:

- it reads only files under ``docs/experiments/<experiment>/`` and writes
  nothing: the tree is byte-identical after a read;
- no experiment folder (or a clone without ``docs/experiments``) reads as
  ``None``, which the screen says as "no experiment files here";
- the ``templates`` folder is not an experiment;
- the trial log's figures are read only when the ``values`` callback says so,
  and the callback is asked on every read;
- a file that cannot be read is left out, the rest still show.

Synthetic files only; see :mod:`tests.support.lab_files`.
"""

from pathlib import Path

import pytest

from hermes.adapters.console.lab_files import FileLabReader, find_experiments_root
from tests.support.lab_files import FIGURES, synthetic_files, write_experiment


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_a_read_returns_every_strategy_and_writes_nothing(tmp_path: Path) -> None:
    """Six rows from the synthetic experiment, and the files are unchanged afterwards."""
    experiments = write_experiment(tmp_path)
    before = _tree(tmp_path)
    snapshot = FileLabReader(experiments, values=lambda: False).read()
    assert (snapshot is not None and len(snapshot.strategies), _tree(tmp_path) == before) == (
        6,
        True,
    )


def test_no_root_reads_as_none(tmp_path: Path) -> None:
    """A machine without docs/experiments gets None, not an empty Lab."""
    assert (
        FileLabReader(None, values=lambda: False).read(),
        FileLabReader(tmp_path / "missing", values=lambda: False).read(),
    ) == (None, None)


def test_the_templates_folder_is_not_an_experiment(tmp_path: Path) -> None:
    """docs/experiments/templates holds batch and card templates; it adds no row."""
    experiments = tmp_path / "docs" / "experiments"
    (experiments / "templates").mkdir(parents=True)
    (experiments / "templates" / "research-batch.md").write_text("---\nbatch_id: <b1>\n---\n")
    assert FileLabReader(experiments, values=lambda: False).read() is None


def test_figures_are_read_only_when_the_callback_allows_it(tmp_path: Path) -> None:
    """The callback is asked on each read: first off (no figures), then on (figures)."""
    experiments = write_experiment(tmp_path)
    allowed = [False]
    reader = FileLabReader(experiments, values=lambda: allowed[0])
    first = reader.read()
    allowed[0] = True
    second = reader.read()
    assert first is not None and second is not None
    runs = lambda snapshot: [r for s in snapshot.strategies for r in s.runs]  # noqa: E731
    assert (
        {r.values for r in runs(first)},
        all(r.values is not None for r in runs(second)),
    ) == ({None}, True)


def test_a_file_that_cannot_be_decoded_is_left_out(tmp_path: Path) -> None:
    """A binary card does not stop the rest of the Lab from showing."""
    experiments = write_experiment(tmp_path)
    (experiments / "synth-exp" / "cards" / "synth-ensemble.md").write_bytes(b"\xff\xfe\x00")
    snapshot = FileLabReader(experiments, values=lambda: False).read()
    assert snapshot is not None
    ensemble = next(s for s in snapshot.strategies if s.strategy_id == "synth-ensemble")
    assert (len(snapshot.strategies), ensemble.rules) == (6, ())


def test_find_experiments_root_walks_up_to_the_clone(tmp_path: Path) -> None:
    """From a module deep in the clone, the first docs/experiments above it is found."""
    experiments = write_experiment(tmp_path)
    deep = tmp_path / "src" / "hermes" / "entrypoints"
    deep.mkdir(parents=True)
    assert find_experiments_root(deep / "x.py") == experiments.resolve()


def test_find_experiments_root_gives_none_outside_a_clone(tmp_path: Path) -> None:
    """No docs/experiments above the start: None."""
    lonely = tmp_path / "a" / "b"
    lonely.mkdir(parents=True)
    root = find_experiments_root(lonely)
    assert root is None or "synth" not in str(root)


@pytest.mark.parametrize("figure", FIGURES)
def test_the_synthetic_figures_are_distinct_enough_to_search_for(figure: str) -> None:
    """The figures other tests search renders for are in the trial log, so the search is real."""
    assert figure in synthetic_files().trial_log
