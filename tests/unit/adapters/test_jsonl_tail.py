"""Unit tests for finding and replacing a torn last JSON line (issue #891).

Invariants under test:

- Only a last line that is not valid JSON is torn; a whole last line, an
  empty file and a valid line missing its newline are not.
- The torn line's number, offset, size and SHA-256 describe exactly the
  bytes a repair drops.
- A replacement keeps every byte before the torn line, ends with the new
  line, and keeps the file's permissions.
"""

import hashlib
import stat
import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.jsonl_tail import TornLine, replace_torn_line, torn_line

_WHOLE = b'{"a": 1}\n{"b": 2}\n'


def test_a_file_whose_last_line_parses_has_no_torn_line(tmp_path: Path) -> None:
    """Two whole lines, each with its newline: nothing to repair."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(_WHOLE)
    assert type(torn_line(path)) is type(None)


def test_a_valid_last_line_without_its_newline_is_not_torn(tmp_path: Path) -> None:
    """A last line that parses is never dropped, even if its newline is missing."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(_WHOLE + b'{"c": 3}')
    assert type(torn_line(path)) is type(None)


def test_an_empty_file_has_no_torn_line(tmp_path: Path) -> None:
    """An empty file is the reader's problem, not a torn write."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(b"")
    assert type(torn_line(path)) is type(None)


def test_a_cut_last_line_is_torn_and_described_by_its_bytes(tmp_path: Path) -> None:
    """Half a line after two whole ones is line 3, starting where line 2 ended."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(_WHOLE + b'{"c": ')
    assert torn_line(path) == TornLine(
        number=3, offset=len(_WHOLE), size=6, sha256=hashlib.sha256(b'{"c": ').hexdigest()
    )


@given(cut=st.integers(min_value=1, max_value=len(b'{"third": [1, 2, 3]}') - 1))
def test_every_cut_of_an_append_is_torn_at_the_same_offset(cut: int) -> None:
    """Wherever a power loss cuts the third line, the repair keeps the first two whole."""
    third = b'{"third": [1, 2, 3]}'[:cut]
    found = _torn_of(_WHOLE + third)
    assert found is not None
    assert (found.number, found.offset, found.size) == (3, len(_WHOLE), cut)


def _torn_of(data: bytes) -> TornLine | None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.jsonl"
        path.write_bytes(data)
        return torn_line(path)


def test_a_replacement_keeps_the_whole_lines_and_appends_the_record(tmp_path: Path) -> None:
    """The torn bytes go, the record line takes their place with its newline."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(_WHOLE + b'{"c": ')
    path.chmod(0o640)
    torn = torn_line(path)
    assert torn is not None
    replace_torn_line(path, torn, '{"kind": "repair"}')
    assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), sorted(tmp_path.iterdir())) == (
        _WHOLE + b'{"kind": "repair"}\n',
        0o640,
        [path],
    )
