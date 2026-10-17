"""Unit tests for the strategy factory (issue #1074, research funnel contract §2a).

Rules under test:

- An entry with no ``signal:`` builds today's Donchian adapter with its
  pre-registered parameters, so v1 and v2 trade exactly as signed.
- An unknown ``signal:`` is refused; it never falls back to the default.
- A parameter mapping overrides the defaults by name; a name the kind does
  not take is refused, so a neighbour replay cannot silently run the base.
"""

from decimal import Decimal

import pytest

from hermes.application.strategy_protocol.strategy_factory import (
    DONCHIAN_BREAKOUT,
    StrategyKind,
    UnknownSignalError,
    known_signals,
    strategy_kind,
)
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter, swing_parameters


class TestKindLookup:
    """``signal:`` names the kind; no key means the Donchian breakout."""

    # SEV-1: v1 and v2 must trade the signed rule after the seam lands (contract §2a)
    def test_no_signal_is_the_donchian_breakout(self) -> None:
        """A v1 or v2 entry has no ``signal:`` key and keeps its adapter."""
        assert strategy_kind(None).signal == DONCHIAN_BREAKOUT

    def test_the_donchian_breakout_can_be_named(self) -> None:
        """Naming the default kind is the same kind as leaving it out."""
        assert strategy_kind(DONCHIAN_BREAKOUT) == strategy_kind(None)

    # SEV-1: an unknown signal must never trade the default rule (contract §2a)
    def test_an_unknown_signal_is_refused(self) -> None:
        """A typo in ``signal:`` stops the build instead of running v1's rule."""
        with pytest.raises(UnknownSignalError, match="donchian-breakuot"):
            strategy_kind("donchian-breakuot")

    def test_the_donchian_breakout_is_listed_first(self) -> None:
        """The default kind leads the list; each candidate PR appends its own kind."""
        assert known_signals()[0] == DONCHIAN_BREAKOUT

    def test_every_known_signal_resolves_to_its_own_kind(self) -> None:
        """A listed signal builds the kind registered under that name, never another."""
        assert [s for s in known_signals() if strategy_kind(s).signal != s] == []


class TestDefaultParameters:
    """The default parameters are the adapter's pre-registered §5 values."""

    def test_defaults_match_the_pre_registered_parameters(self) -> None:
        """Rendered as strings, they are exactly what the gates fingerprint."""
        defaults = strategy_kind(None).default_parameters()
        assert {name: str(value) for name, value in defaults.items()} == swing_parameters()

    def test_defaults_are_a_fresh_copy(self) -> None:
        """A caller that edits the mapping does not change the next build."""
        kind = strategy_kind(None)
        kind.default_parameters()["donchian_period"] = 99
        assert kind.default_parameters()["donchian_period"] == 20


class _Probe:
    """A builder that records the parameters it was built with."""

    def __init__(self, *, lookback: int = 20, multiple: Decimal = Decimal("3.0")) -> None:
        self.built_with = {"lookback": lookback, "multiple": multiple}


_PROBE = StrategyKind(signal="probe", build=_Probe)


class TestCreate:
    """``create`` builds a new adapter from the kind and a parameter mapping."""

    def test_no_parameters_builds_with_the_defaults(self) -> None:
        """With no mapping the builder gets its own keyword defaults."""
        assert _PROBE.create().built_with == {"lookback": 20, "multiple": Decimal("3.0")}

    def test_a_parameter_overrides_its_default(self) -> None:
        """A neighbour replay's override reaches the builder; the rest stay default."""
        built = _PROBE.create({"multiple": Decimal("2.5")})
        assert built.built_with == {"lookback": 20, "multiple": Decimal("2.5")}

    def test_each_build_is_a_new_adapter(self) -> None:
        """Adapters hold bar history, so pairs never share one."""
        assert strategy_kind(None).create() is not strategy_kind(None).create()

    def test_the_default_kind_builds_the_swing_adapter(self) -> None:
        """The Donchian breakout kind is today's adapter, not a copy of it."""
        assert type(strategy_kind(None).create()) is SwingStrategyAdapter

    def test_a_parameter_the_kind_does_not_take_is_refused(self) -> None:
        """A card parameter name that does not match the module fails the build."""
        with pytest.raises(ValueError, match="k_agree"):
            strategy_kind(None).create({"k_agree": 2})

    def test_the_default_parameters_are_the_builders_keyword_defaults(self) -> None:
        """The driver records these as the base trial's parameters."""
        assert _PROBE.default_parameters() == {"lookback": 20, "multiple": Decimal("3.0")}

    def test_only_keyword_only_parameters_are_defaults(self) -> None:
        """Only keyword-only parameters are card parameters; positional and ``**`` ones are not."""

        def build(lookback: int = 5, *, multiple: int = 3, **extra: int) -> tuple[int, int]:
            return lookback, multiple

        kind = StrategyKind(signal="mixed", build=build)
        assert kind.default_parameters() == {"multiple": 3}
