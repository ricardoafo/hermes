"""Console read-model adapters: packaged demo scenarios today, the ledger later."""

from hermes.adapters.console.fixture_read_model import (
    FixtureReadModel,
    ScenarioError,
    UnknownScenarioError,
    available_scenarios,
)

__all__ = ["FixtureReadModel", "ScenarioError", "UnknownScenarioError", "available_scenarios"]
