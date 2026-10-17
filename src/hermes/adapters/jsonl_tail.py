"""Find and replace a torn last line in a hash-chained JSON Lines file.

A power loss or kill in the middle of an append can leave a half-written last
line. Every later read of the trial log or the decision journal then stops at
that line, and every later append verifies the chain first, so nothing can be
written again (issue #891).

Only a last line that is not valid JSON counts as torn: that is what a cut
write leaves. A line that parses but breaks the chain is an edit, and is never
touched here. The replacement is written to a temporary file beside the
original, fsynced and renamed over it, so a crash during the repair leaves
either the torn file or the repaired one, never a third state.

Reference: issue #891.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = ["TornLine", "replace_torn_line", "torn_line"]


@dataclass(frozen=True, slots=True)
class TornLine:
    """A last line that is not valid JSON.

    Attributes:
        number: Its 1-based line number.
        offset: Byte offset where it starts; everything before it is kept.
        size: Its length in bytes, without a trailing newline.
        sha256: SHA-256 of its bytes, so the record of the drop names them.
    """

    number: int
    offset: int
    size: int
    sha256: str


def torn_line(path: Path) -> TornLine | None:
    """Return the torn last line of ``path``, or ``None`` when the last line parses.

    Raises:
        OSError: The file cannot be read.
    """
    data = path.read_bytes()
    body = data.removesuffix(b"\n")
    offset = body.rfind(b"\n") + 1
    last = body[offset:]
    if not last.strip():
        return None
    try:
        json.loads(last)
    except ValueError:
        return TornLine(
            number=body.count(b"\n") + 1,
            offset=offset,
            size=len(last),
            sha256=hashlib.sha256(last).hexdigest(),
        )
    return None


def replace_torn_line(path: Path, torn: TornLine, line: str) -> None:
    """Replace ``torn`` with ``line`` in one atomic rename.

    Args:
        path: The file :func:`torn_line` read.
        torn: Its torn last line.
        line: The JSON line recording the drop, without a newline.

    Raises:
        OSError: The replacement cannot be written.
    """
    kept = path.read_bytes()[: torn.offset]
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".repair")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(kept + line.encode("utf-8") + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if os.name == "nt":
        # Windows cannot open a directory to fsync it; NTFS journals the rename.
        return
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
