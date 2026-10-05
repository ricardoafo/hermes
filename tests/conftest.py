"""Shared pytest fixtures, plugin registration and the Hypothesis profiles.

The sev1_guard, sentry_guard, network_guard and structlog_guard plugins are registered
here (not via addopts -p) because pytest loads conftest.py from the rootdir before
importing plugins, so it can resolve the `tests.plugins.sev1_guard` module by path.

Hypothesis runs derandomized by default (#637): the PR and merge-queue gates
must give the same verdict on the same commit, and random seeds made them
fail at random. The per-example deadline is off in the gates for the same
reason: a loaded runner made the first example of a fast test overrun 200ms
and fail on timing, not behaviour. ``HYPOTHESIS_PROFILE=explore`` brings
random seeds and the deadline back for a run whose job is to search for new
counterexamples and slow tests.
"""

import os

from hypothesis import settings

pytest_plugins = [
    "tests.plugins.sev1_guard",
    "tests.plugins.sentry_guard",
    "tests.plugins.network_guard",
    "tests.plugins.structlog_guard",
    "pytester",  # enables the `pytester` fixture used by test_sev1_guard.py
]

settings.register_profile("ci", derandomize=True, database=None, deadline=None, print_blob=True)
settings.register_profile("explore", derandomize=False, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
