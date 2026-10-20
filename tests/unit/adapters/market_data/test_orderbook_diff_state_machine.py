"""Stateful property test: L2 snapshot plus depth diffs against a dict model (issue #844).

How does Hermes fail: the exchange sends a REST depth snapshot and then a
stream of ``depthUpdate`` diffs; some arrive after a gap (events lost on the
wire) and some arrive again or out of order. A local book that applies a gapped
or replayed diff silently drifts from the exchange's book.

No code in ``src/`` applies diffs to a book: the only diff logic is the
sequence gate inside :meth:`BinanceLiveWsAdapter.stream_orderbook_diffs` plus
the wire parsers (``_parse_snapshot``, ``_parse_depth_update``). The machine
therefore drives the real stream over a fake WebSocket (no network), keeps the
exchange's true book in a dict, and builds the local book only from the
snapshot and the diffs the real stream yields. The applying itself is the
test's few lines; what is under test is that the stream yields exactly the
contiguous diffs, parsed faithfully, and refuses the rest.

Reference: SA-3 §Binance L2 snapshot procedure, spec-4c-market-data-adapters
change.md D-2, issue #844.
"""

import asyncio
import json
import os
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator
from decimal import Decimal
from types import SimpleNamespace, TracebackType
from typing import Any, Self, cast

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

import hermes.adapters.market_data.binance_live_ws as ws_module
from hermes.adapters.market_data.binance_live_ws import BinanceLiveWsAdapter, _parse_snapshot
from hermes.domain.errors import InfraError
from hermes.domain.market_data.orderbook_diff import OrderbookDiff
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_EXPLORE = os.environ.get("HYPOTHESIS_PROFILE") == "explore"
_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
# Bids live strictly below asks so no diff can cross the book.
_BID_PRICES = tuple(Decimal(p) for p in range(90, 100))
_ASK_PRICES = tuple(Decimal(p) for p in range(101, 111))

type Book = dict[Decimal, Decimal]


class _FakeSocket:
    """An open WebSocket whose messages are whatever the test queued."""

    def __init__(self, inbox: deque[str]) -> None:
        self._inbox = inbox

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if not self._inbox:
            raise StopAsyncIteration
        return self._inbox.popleft()


def _levels(prices: tuple[Decimal, ...]) -> st.SearchStrategy[list[tuple[Decimal, Decimal]]]:
    """Level updates on one side; quantity 0 deletes the level."""
    qty = st.integers(min_value=0, max_value=50).map(lambda n: Decimal(n) / 10)
    return st.lists(st.tuples(st.sampled_from(prices), qty), max_size=4, unique_by=lambda t: t[0])


def _wire(levels: list[tuple[Decimal, Decimal]]) -> list[list[str]]:
    return [[str(price), str(qty)] for price, qty in levels]


def _apply(book: Book, levels: list[tuple[Decimal, Decimal]]) -> None:
    for price, qty in levels:
        if qty == 0:
            book.pop(price, None)
        else:
            book[price] = qty


def _apply_diff(bids: Book, asks: Book, diff: OrderbookDiff) -> None:
    for side, levels in ((bids, diff.bids), (asks, diff.asks)):
        for level in levels:
            if level.delete:
                side.pop(level.price, None)
            else:
                side[level.price] = level.quantity


class OrderbookDiffMachine(RuleBasedStateMachine):
    """The local book built from the stream equals the exchange's book while the stream lives.

    Invariants protected (SA-3 steps 4-5):

    - every diff the stream yields starts at the previous yielded diff's final id + 1;
    - a gapped diff and a replayed (out-of-order) diff are refused with ``InfraError``;
    - after a refusal the stream yields nothing more until a re-snapshot;
    - a diff the snapshot already contains never reaches the book, and the
      first diff kept straddles ``lastUpdateId + 1`` (issue #866);
    - while the stream lives, snapshot + yielded diffs == the exchange's book.
    """

    def __init__(self) -> None:
        """Open a fake socket, take the first snapshot and start the stream."""
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.inbox: deque[str] = deque()
        self.saved_websockets = ws_module.websockets  # type: ignore[attr-defined]
        inbox = self.inbox
        ws_module.websockets = SimpleNamespace(connect=lambda _url: _FakeSocket(inbox))  # type: ignore[attr-defined]
        self.adapter = BinanceLiveWsAdapter.create()
        self.exchange_bids: Book = {Decimal(95): Decimal(1)}
        self.exchange_asks: Book = {Decimal(105): Decimal(1)}
        self.exchange_id = 1000
        self.last_sent: dict[str, Any] | None = None
        self._resnapshot()

    def teardown(self) -> None:
        """Close the stream and the loop, and put the real module back."""
        self.loop.run_until_complete(self.stream.aclose())
        self.loop.close()
        ws_module.websockets = self.saved_websockets  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _resnapshot(self) -> None:
        snapshot = _parse_snapshot(
            data={
                "lastUpdateId": self.exchange_id,
                "bids": [[str(p), str(q)] for p, q in sorted(self.exchange_bids.items())[::-1]],
                "asks": [[str(p), str(q)] for p, q in sorted(self.exchange_asks.items())],
            },
            pair=_PAIR,
        )
        self.local_bids: Book = {lvl.price: lvl.quantity for lvl in snapshot.bids}
        self.local_asks: Book = {lvl.price: lvl.quantity for lvl in snapshot.asks}
        self.inbox.clear()
        self.stream = cast(
            "AsyncGenerator[OrderbookDiff]",
            self.adapter.stream_orderbook_diffs(_PAIR, after_update_id=snapshot.last_update_id),
        )
        self.alive = True
        self.prev_final: int | None = None

    def _message(
        self,
        first: int,
        final: int,
        bids: list[tuple[Decimal, Decimal]],
        asks: list[tuple[Decimal, Decimal]],
    ) -> dict[str, Any]:
        return {
            "e": "depthUpdate",
            "E": 1704067200000,
            "s": "BTCUSDT",
            "U": first,
            "u": final,
            "b": _wire(bids),
            "a": _wire(asks),
        }

    def _deliver(self, *messages: dict[str, Any]) -> OrderbookDiff | InfraError | None:
        self.inbox.extend(json.dumps(message) for message in messages)
        try:
            return self.loop.run_until_complete(anext(self.stream))
        except InfraError as exc:
            return exc
        except StopAsyncIteration:
            return None

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------

    @precondition(lambda self: self.alive)
    @rule(
        span=st.integers(min_value=0, max_value=3),
        bids=_levels(_BID_PRICES),
        asks=_levels(_ASK_PRICES),
    )
    def contiguous_diff(
        self,
        span: int,
        bids: list[tuple[Decimal, Decimal]],
        asks: list[tuple[Decimal, Decimal]],
    ) -> None:
        """Send the next diff in sequence: it is yielded unchanged and the books agree.

        The first diff after a snapshot starts at ``lastUpdateId + 1`` here;
        ``stale_diffs_then_straddling_diff`` covers the other first diffs.
        """
        first = self.exchange_id + 1
        final = first + span
        message = self._message(first, final, bids, asks)
        _apply(self.exchange_bids, bids)
        _apply(self.exchange_asks, asks)
        self.exchange_id = final
        self.last_sent = message
        got = self._deliver(message)
        assert isinstance(got, OrderbookDiff), got
        assert (got.first_update_id, got.final_update_id) == (first, final)
        assert [(lvl.price, lvl.quantity, lvl.delete) for lvl in got.bids] == [
            (p, q, q == 0) for p, q in bids
        ]
        assert [(lvl.price, lvl.quantity, lvl.delete) for lvl in got.asks] == [
            (p, q, q == 0) for p, q in asks
        ]
        if self.prev_final is not None:
            assert got.first_update_id == self.prev_final + 1
        self.prev_final = got.final_update_id
        _apply_diff(self.local_bids, self.local_asks, got)

    @precondition(lambda self: self.alive and self.prev_final is None)
    @rule(
        stale=st.lists(
            st.tuples(st.integers(min_value=0, max_value=3), _levels(_BID_PRICES)),
            min_size=1,
            max_size=3,
        ),
        back=st.integers(min_value=0, max_value=3),
        span=st.integers(min_value=0, max_value=3),
        bids=_levels(_BID_PRICES),
    )
    def stale_diffs_then_straddling_diff(
        self,
        stale: list[tuple[int, list[tuple[Decimal, Decimal]]]],
        back: int,
        span: int,
        bids: list[tuple[Decimal, Decimal]],
    ) -> None:
        """Diffs the snapshot already holds are dropped; the straddling one is yielded.

        The stale diffs end at or before ``lastUpdateId`` and carry arbitrary
        levels, so yielding one would put old quantities back into the book.
        """
        snapshot_id = self.exchange_id
        stale_messages = [
            self._message(snapshot_id - age - 1, snapshot_id - age, levels, [])
            for age, levels in stale
        ]
        first = snapshot_id - back
        final = snapshot_id + 1 + span
        _apply(self.exchange_bids, bids)
        self.exchange_id = final
        message = self._message(first, final, bids, [])
        self.last_sent = message
        got = self._deliver(*stale_messages, message)
        assert isinstance(got, OrderbookDiff), got
        assert (got.first_update_id, got.final_update_id) == (first, final)
        self.prev_final = final
        _apply_diff(self.local_bids, self.local_asks, got)

    @precondition(lambda self: self.alive and self.prev_final is None)
    @rule(missed=st.integers(min_value=1, max_value=5), bids=_levels(_BID_PRICES))
    def first_diff_after_a_gap_since_the_snapshot(
        self, missed: int, bids: list[tuple[Decimal, Decimal]]
    ) -> None:
        """Send a first diff past ``lastUpdateId + 1``: events were lost, so it is refused."""
        snapshot_id = self.exchange_id
        first = snapshot_id + 1 + missed
        _apply(self.exchange_bids, bids)
        self.exchange_id = first
        got = self._deliver(self._message(first, first, bids, []))
        assert isinstance(got, InfraError), got
        assert f"Expected first_update_id<={snapshot_id + 1}" in str(got)
        self.alive = False

    @precondition(lambda self: self.alive and self.prev_final is not None)
    @rule(
        missed=st.integers(min_value=1, max_value=5),
        lost_bids=_levels(_BID_PRICES),
        bids=_levels(_BID_PRICES),
    )
    def gapped_diff(
        self,
        missed: int,
        lost_bids: list[tuple[Decimal, Decimal]],
        bids: list[tuple[Decimal, Decimal]],
    ) -> None:
        """Lose ``missed`` events on the wire, then send one: the stream refuses it."""
        _apply(self.exchange_bids, lost_bids)
        first = self.exchange_id + 1 + missed
        _apply(self.exchange_bids, bids)
        self.exchange_id = first
        got = self._deliver(self._message(first, first, bids, []))
        assert isinstance(got, InfraError), got
        assert self.prev_final is not None
        assert f"Expected first_update_id={self.prev_final + 1}" in str(got)
        self.alive = False

    @precondition(lambda self: self.alive and self.last_sent is not None)
    @rule()
    def replayed_diff(self) -> None:
        """Deliver the last diff again (out of order): the stream refuses it."""
        assert self.last_sent is not None
        got = self._deliver(self.last_sent)
        assert isinstance(got, InfraError), got
        assert f"got {self.last_sent['U']}" in str(got)
        self.alive = False

    @precondition(lambda self: not self.alive)
    @rule(bids=_levels(_BID_PRICES))
    def diff_after_refusal(self, bids: list[tuple[Decimal, Decimal]]) -> None:
        """After a refusal even a well-sequenced diff is not yielded."""
        got = self._deliver(self._message(self.exchange_id + 1, self.exchange_id + 1, bids, []))
        assert got is None

    @rule(moved=st.integers(min_value=0, max_value=5))
    def resnapshot(self, moved: int) -> None:
        """Take a fresh snapshot (the exchange may have moved on) and restart the stream."""
        self.loop.run_until_complete(self.stream.aclose())
        self.exchange_id += moved
        self.last_sent = None
        self._resnapshot()

    # ------------------------------------------------------------------
    # invariants
    # ------------------------------------------------------------------

    @invariant()
    def local_book_matches_exchange_while_alive(self) -> None:
        """Snapshot plus yielded diffs reproduce the exchange's book exactly."""
        if self.alive:
            assert (self.local_bids, self.local_asks) == (self.exchange_bids, self.exchange_asks)

    @invariant()
    def local_book_is_never_crossed(self) -> None:
        """Keep the local best bid below the local best ask."""
        if self.local_bids and self.local_asks:
            assert max(self.local_bids) < min(self.local_asks)


def test_a_diff_older_than_the_snapshot_never_reaches_the_book() -> None:
    """With a snapshot at 1000, a diff U=990 u=995 is dropped and U=998 u=1003 is yielded.

    Issue #866: the snapshot used to drop ``lastUpdateId``, so the stale diff
    was yielded and its old quantities went back into the book.
    """
    machine = OrderbookDiffMachine()
    try:
        got = machine._deliver(
            machine._message(990, 995, [(Decimal(95), Decimal(7))], []),
            machine._message(998, 1003, [(Decimal(96), Decimal(2))], []),
        )
    finally:
        machine.teardown()
    assert isinstance(got, OrderbookDiff), got
    assert (got.first_update_id, got.final_update_id, got.bids[0].price) == (
        998,
        1003,
        Decimal(96),
    )


OrderbookDiffMachine.TestCase.settings = settings(
    stateful_step_count=60 if _EXPLORE else 25,
)
TestOrderbookDiffMachine = OrderbookDiffMachine.TestCase
