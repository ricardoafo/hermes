"""Scenario suite: every engine path through the real ``hermes serve`` (issue #689).

Each scenario scripts a synthetic market (:mod:`.markets`), runs the production
serve composition on the fake market from issue #688, and checks what reached
the ledger and the notifier. Expected quantities, fees and prices are worked out
in the test from the pre-registration, never read back from the code under test.

``rules.py`` maps every numbered pre-registration rule to the scenario that
proves it, or to the open issue that explains why it is not wired yet.
"""
