"""Check that a pytest node id names a test that exists.

Shared by the lists that point at their proofs: the pre-registration rule list
(``tests/integration/scenarios/rules.py``) and the failure-mode catalogue
(``tests/integration/scenarios/failure_modes.py``). A renamed or deleted test
must fail those lists instead of leaving an entry pointing at nothing.
"""

import importlib

from tests.support.paths import REPO_ROOT

PROJECT_ROOT = REPO_ROOT


def names_a_test(node_id: str) -> bool:
    """Return whether ``path::[Class::]function`` names a test function in this repo.

    ``runTest`` counts: it is the one test a Hypothesis state machine's ``TestCase`` runs.
    """
    path, *names = node_id.split("::")
    if not names or not (PROJECT_ROOT / path).is_file():
        return False
    target: object = importlib.import_module(path.removesuffix(".py").replace("/", "."))
    for name in names:
        target = getattr(target, name, None)
        if target is None:
            return False
    return callable(target) and (names[-1].startswith("test_") or names[-1] == "runTest")
