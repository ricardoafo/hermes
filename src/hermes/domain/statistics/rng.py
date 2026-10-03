"""Deterministic random source for resampling statistics.

SplitMix64 is a small, fully specified generator: the same seed gives the
same stream on every platform and Python version, which keeps bootstrap
results reproducible for audit.
"""

from __future__ import annotations

__all__ = ["SplitMix64"]


class SplitMix64:
    """SplitMix64: a small, fully specified generator, so a seed reproduces the
    interval exactly regardless of the Python version's ``random`` internals."""

    _MASK = (1 << 64) - 1

    def __init__(self, seed: int) -> None:
        self._state = seed & self._MASK

    def next64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & self._MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self._MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self._MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        """Return a uniform integer in [0, n) by rejection sampling."""
        limit = (1 << 64) - (1 << 64) % n
        while True:
            value = self.next64()
            if value < limit:
                return value % n
