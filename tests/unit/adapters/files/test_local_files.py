"""Tests for the local file system behind the files port (issue #1186).

Invariants:

- Reads return the file's exact bytes, and its text decoded as UTF-8.
- An append adds the text after the existing content and never creates a
  missing file.
- ``sha256`` is the digest of the whole file, however many reads it takes.
- ``files_under`` lists every file at any depth in sorted order, and no
  directory.
"""

import hashlib
from pathlib import Path

import pytest

from hermes.adapters.files import LocalFiles


def test_reads_return_the_exact_bytes_and_the_utf8_text(tmp_path: Path) -> None:
    """Bytes are untouched; text is decoded as UTF-8 whatever the locale."""
    path = tmp_path / "notes.md"
    path.write_bytes("café · 1\n".encode())
    files = LocalFiles()

    assert (files.read_bytes(path), files.read_text(path)) == (
        "café · 1\n".encode(),
        "café · 1\n",
    )


def test_an_append_lands_after_the_existing_text(tmp_path: Path) -> None:
    """The lab notebook only grows: earlier text is kept byte for byte."""
    path = tmp_path / "lab-notebook.md"
    path.write_text("# Notebook\n", encoding="utf-8")

    LocalFiles().append_text(path, "### entry ✓\n")

    assert path.read_text(encoding="utf-8") == "# Notebook\n### entry ✓\n"


def test_an_append_never_creates_a_missing_file(tmp_path: Path) -> None:
    """A notebook must exist before anything is appended to it."""
    path = tmp_path / "missing.md"

    with pytest.raises(FileNotFoundError):
        LocalFiles().append_text(path, "### entry\n")

    assert path.exists() is False


def test_sha256_covers_a_file_larger_than_one_read(tmp_path: Path) -> None:
    """A file over the 64 KiB read size hashes to the digest of all its bytes."""
    payload = bytes(range(256)) * 600
    path = tmp_path / "data.bin"
    path.write_bytes(payload)

    assert LocalFiles().sha256(path) == hashlib.sha256(payload).hexdigest()


def test_files_under_lists_every_file_in_sorted_order(tmp_path: Path) -> None:
    """Nested files are listed, directories are not, in sorted path order."""
    (tmp_path / "b" / "deep").mkdir(parents=True)
    for name in ("b/deep/z.txt", "a.txt", "b/c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    listed = LocalFiles().files_under(tmp_path)

    assert [p.relative_to(tmp_path).as_posix() for p in listed] == [
        "a.txt",
        "b/c.txt",
        "b/deep/z.txt",
    ]


def test_exists_is_dir_and_resolve_follow_the_disk(tmp_path: Path) -> None:
    """A file exists but is no directory; a symbolic link resolves to its target."""
    target = tmp_path / "target.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    files = LocalFiles()

    assert (
        files.exists(target),
        files.is_dir(target),
        files.is_dir(tmp_path),
        files.exists(tmp_path / "none"),
        files.resolve(link),
    ) == (True, False, True, False, target.resolve())
