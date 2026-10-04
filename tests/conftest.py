"""Shared pytest fixtures + plugin registration.

The sev1_guard and sentry_guard plugins are registered here (not via addopts -p) because pytest
loads conftest.py from the rootdir before importing plugins, so it can resolve
the `tests.plugins.sev1_guard` module by path.
"""

pytest_plugins = [
    "tests.plugins.sev1_guard",
    "tests.plugins.sentry_guard",
    "pytester",  # enables the `pytester` fixture used by test_sev1_guard.py
]
