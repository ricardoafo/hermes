"""Types shared by the smoke fixtures and tests."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteResult:
    """Outcome of one command run on hermes-01."""

    returncode: int
    stdout: str
    stderr: str


RunOnVps = Callable[[str], RemoteResult]
