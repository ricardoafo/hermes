"""Where the repository is, for tests that read its shipped files.

One constant instead of a ``Path(__file__).parents[n]`` per module, whose
``n`` depends on how deep the test sits (issue #1182).
"""

from pathlib import Path
from typing import Final

__all__ = ["REPO_ROOT"]

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
"""The repository root: the directory holding ``pyproject.toml``."""
