"""Shared pytest fixtures, plugin registration and the Hypothesis profiles.

The sev1_guard, sentry_guard and network_guard plugins are registered here (not via addopts -p)
because pytest loads conftest.py from the rootdir before importing plugins, so it can resolve
the `tests.plugins.sev1_guard` module by path.

Hypothesis runs derandomized by default (#637): the PR and merge-queue gates
must give the same verdict on the same commit, and random seeds made them
fail at random. ``HYPOTHESIS_PROFILE=explore`` brings random seeds back for
a run whose job is to search for new counterexamples.
"""

import os

from hypothesis import settings

pytest_plugins = [
    "tests.plugins.sev1_guard",
    "tests.plugins.sentry_guard",
    "tests.plugins.network_guard",
    "pytester",  # enables the `pytester` fixture used by test_sev1_guard.py
]

settings.register_profile("deterministic", derandomize=True, database=None, print_blob=True)
settings.register_profile("explore", derandomize=False, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "deterministic"))
