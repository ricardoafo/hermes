"""Find the repository checkout and read its signed pre-registration from there.

The application layer reads no file and finds no directory on its own
(issue #1186): the entrypoint or script that builds a
:class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`
names the project root. A release names it from the folder beside its
``config/`` (``serve.pre_registration_for``); a developer checkout and the
scripts find it here, by walking up to the ``pyproject.toml``.

Reference: GitHub issue #1186; issue #923 (an installed package has no
repository to search from).
"""

from pathlib import Path

from hermes.adapters.files import LocalFiles
from hermes.application.strategy_protocol.preregistration import PreRegistration

__all__ = ["find_project_root", "load_pre_registration"]


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest directory at or above ``start`` that holds ``pyproject.toml``.

    Args:
        start: Where to begin; defaults to this module's directory, which is
            inside the checkout when hermes runs from one.

    Raises:
        FileNotFoundError: No ``pyproject.toml`` up to the file system root,
            as in an installed package.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(
        f"Could not detect project root: no pyproject.toml found searching upward from {here}."
    )


def load_pre_registration(project_root: Path | None = None) -> PreRegistration:
    """Return the signed pre-registration under ``project_root``, read from the local disk.

    Args:
        project_root: The directory that holds ``docs/experiments/``; defaults
            to :func:`find_project_root`.

    Raises:
        FileNotFoundError: No project root, or no pre-registration under it.
        ValueError: The file is not the v1 pre-registration.
    """
    root = project_root if project_root is not None else find_project_root()
    return PreRegistration(root, files=LocalFiles())
