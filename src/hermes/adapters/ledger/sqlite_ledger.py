"""SQLite ledger adapter (``LedgerPort``, ``TradeLedgerPort``, ``TaxTradeSourcePort``).

Concrete adapter for the FIFO tax-lot ledger. Uses stdlib ``sqlite3``
(no ORM — ADR-003 rules out heavyweight tooling for the single-writer
workload). Every mutating method groups its row insert and the accompanying
event append into ONE ``BEGIN ... COMMIT`` boundary so invariant I-06 is
satisfied by construction.

Serialization of :class:`DomainEvent` values is done via ``dataclasses.asdict``
plus a small canonicaliser that stringifies :class:`Decimal`, :class:`datetime`,
and value-object wrappers into JSON-safe primitives. The stored payload plus
the ``event_schema_version`` column let a future upcaster chain
(code-conventions §10) reconstruct the event.

Trade rows (:meth:`SqliteLedger.record_trade`) carry EUR values or a pending
status. Lots and consumptions linked to trades are a projection: every trade
write replays the asset's trades through
:func:`~hermes.domain.lots.eur_fifo.replay_fifo_eur` and rewrites their EUR
values, fees included, inside the same transaction (change
``trade-eur-values`` D-6). Once a capital seed exists for a quote currency
(:meth:`SqliteLedger.record_capital_seed`), that currency is replayed too, as the
quote leg of every trade quoted in it (change ``405-usdt-fifo``).

The class is a facade: each table group lives in its own store module
(``_trades``, ``_seeds``, ``_marks``, ``_bars``, ``_events``) on a shared
``_LedgerCore``, and the row codecs live in :mod:`~hermes.adapters.ledger.codecs`
(issue #1185).

Reference: ADR-003 §00 (I-06), §03 (persistence schema),
code-conventions.md §6 (Decimal ledger values), §9 (UTC timestamps).
"""

from hermes.adapters.ledger._bars import _BarStore
from hermes.adapters.ledger._events import _EventStore
from hermes.adapters.ledger._marks import _MarkStore
from hermes.adapters.ledger._seeds import _SeedStore
from hermes.adapters.ledger._trades import _TradeStore

__all__ = ["SqliteLedger"]


class SqliteLedger(_TradeStore, _SeedStore, _MarkStore, _BarStore, _EventStore):
    """SQLite implementation of :class:`~hermes.ports.ledger.LedgerPort` and
    :class:`~hermes.ports.ledger.TradeLedgerPort`.

    The adapter owns its :class:`sqlite3.Connection`. Callers pass a
    pre-opened connection at construction time so tests can inject
    ``sqlite3.connect(":memory:")`` and production injects a file connection
    from :func:`~hermes.adapters.ledger.connection.open_ledger`.

    Threading: not thread-safe (matches ADR-003 §03 single-writer model).
    The application uses one process, one writer at a time.
    """
