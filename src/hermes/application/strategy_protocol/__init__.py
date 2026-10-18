"""Strategy protocol guardrails — pre-registration enforcement layer.

This package implements the five scientific guardrails that make every
subsequent spec's outputs admissible as pre-certification evidence for
the Swing MVP1 experiment (``swing-mvp1-v1``).

Modules:

- :mod:`preregistration` — typed accessor layer over the signed pre-registration doc
- :mod:`data_split_enforcer` — OOS read-refusal enforcer wrapping ``MarketDataPort``
- :mod:`~hermes.application.calibration.certification_gates` holds the §9 checks
  (``section9_checks``)
- :mod:`kill_trigger_evaluator` — §10 kill-criteria evaluator
- :mod:`lab_notebook` — append-only lab notebook writer

Reference: ``openspec/changes/spec-9-strategy-development-protocol/change.md``,
``docs/experiments/swing-mvp1/pre-registration-v1.md``.
"""

from __future__ import annotations
