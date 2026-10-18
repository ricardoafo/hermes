"""Tests for the console's Tax screen (issue #719, design spec screen 4).

Reference: openspec/changes/719-tax-screen/change.md (R-1..R-7).
"""

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from rich.console import RenderableType
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.console.format import MINUS
from hermes.application.console.render import MADRID, to_plain_text
from hermes.application.console.views import TaxBand, TaxEstimate, TaxPending
from hermes.application.filing.pack import FROZEN_FILE, MANIFEST_FILE
from hermes.application.tax.estimate import TaxYearEstimate
from hermes.application.tax.screen import (
    LotSale,
    LotView,
    TaxpayerProfileView,
    TaxScreen,
    bracket_fills,
)
from hermes.domain.tax.two_month import TwoMonthFlag
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_query import QueryInput, QueryList, SortableTable
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_tax import (
    EDIT_LATER,
    LOTS,
    MASK,
    PACK_LINE,
    PROFILE,
    SUMMARY,
    TaxPackStatus,
    TaxTab,
    lot_detail_text,
    lot_rows,
    pack_status,
    pack_text,
    profile_text,
    profile_view,
    summary_text,
    tax_inputs,
)
from hermes.entrypoints.tui_theme import BASE
from hermes.entrypoints.vps import REMOTE_ENV
from tests.unit.domain.tax.scales import SCALE_2026

_D = Decimal
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)


def _lot(ref: str, coin: str, days_ago: int, *, sales: tuple[LotSale, ...] = ()) -> LotView:
    return LotView(
        ref=ref,
        coin=coin,
        bought_at=_FROZEN - timedelta(days=days_ago),
        qty=_D(1),
        remaining=_D(0) if sales else _D(1),
        cost=_D("100.00"),
        fee=_D("0.10"),
        sales=sales,
    )


_SALE = LotSale(
    ref="s-1",
    at=_FROZEN - timedelta(days=20),
    qty=_D(1),
    proceeds=_D("80.00"),
    cost=_D("100.00"),
    fee=_D("0.20"),
    gain=_D("-20.20"),
    two_month=True,
)
_LOTS = (
    _lot("b-1", "BTC", 40),
    _lot("b-2", "SOL", 70, sales=(_SALE,)),
    _lot("b-3", "ETH", 100),
)


def _known_screen() -> TaxScreen:
    """Return a screen whose figures no recomputation from its lots could produce."""
    return TaxScreen(
        year=2026,
        estimate=TaxYearEstimate(
            year=2026,
            realised_gain=_D("777.77"),
            hermes_tax=TaxEstimate(amount=_D("123.45")),
            today_tax=TaxEstimate(amount=_D("1.11")),
            tax_if_sold_now=TaxEstimate(amount=_D("45.67")),
            per_position_if_sold_now=(),
            carry_forward_used=_D(0),
            carry_forward_left=(),
            band=TaxBand(rate=_D(19), next_rate=_D(21), room_to_next=_D("5222.23")),
            flags=(),
            notices=(),
            is_shadow=True,
            savings_base=_D("777.77"),
            two_month=(
                TwoMonthFlag(
                    asset="SOL",
                    sale_ref="s-1",
                    sold_on=(_FROZEN - timedelta(days=20)).date(),
                    loss=_D("20.20"),
                    bought_on=(_FROZEN - timedelta(days=5)).date(),
                ),
            ),
        ),
        fees=_D("8.88"),
        brackets=bracket_fills(SCALE_2026, _D("777.77")),
        lots=_LOTS,
    )


class _FakeTaxSource:
    """Returns a fixed screen and records the prices it was asked with."""

    def __init__(self, screen: TaxScreen | TaxPending) -> None:
        self.screen = screen
        self.prices: list[Mapping[str, Decimal]] = []

    def tax_screen(
        self, *, as_of: datetime, prices: Mapping[str, Decimal]
    ) -> TaxScreen | TaxPending:
        del as_of
        self.prices.append(prices)
        return self.screen


def _plain(content: RenderableType, width: int = 120) -> str:
    return to_plain_text(content, width=width)


# ---------------------------------------------------------------------------
# Renderers


def test_summary_shows_the_service_figures_as_given() -> None:
    """Invariant: every Summary figure is the tax service's, not one derived from the lots."""
    text = _plain(summary_text(_known_screen(), BASE, money=True, width=120))
    assert "realised gains          +€777.77" in text
    assert "fees                    €8.88" in text
    assert "taxable base            €777.77" in text
    assert "estimated tax (FIFO)    €123.45" in text
    assert "open if sold now        €45.67" in text
    assert "€5,222.23 left before 21%" in text


def test_summary_hides_every_amount_when_money_may_not_show() -> None:
    """Invariant: without ``pnl_visible`` no euro amount reaches the Summary."""
    text = _plain(summary_text(_known_screen(), BASE, money=False, width=120))
    assert "€" not in text
    assert "estimated tax (FIFO)    hidden" in text


def test_summary_lists_the_two_month_flag_and_the_coins_it_covers() -> None:
    """Invariant: the Summary names the flagged SOL loss and says USDT is never flagged."""
    text = _plain(summary_text(_known_screen(), BASE, money=True, width=120))
    assert "Two-month rule · BTC, ETH and SOL only, never USDT" in text
    assert f"⚑ SOL loss {MINUS}€20.20 on 2026-09-13, bought again 2026-09-28" in text


def test_a_pending_estimate_shows_its_reason() -> None:
    """Invariant: a pending estimate shows its label instead of figures."""
    pending = TaxPending(label="tax pending: scale missing for 2026")
    assert _plain(summary_text(pending, BASE, money=True, width=80)).strip() == pending.label


def test_lot_rows_carry_the_shared_facets() -> None:
    """Invariant: each lot row has year, month, day, coin and state for the shared query bar."""
    row = lot_rows(_LOTS[1:2], MADRID, money=True)[0]
    assert (row["year"], row["month"], row["coin"], row["state"]) == (
        2026,
        "2026-07",
        "SOL",
        "closed",
    )
    assert (row["cost"], row["gain"], row["flag"]) == (_D("100.00"), _D("-20.20"), "⚑")


def test_lot_rows_drop_money_when_it_may_not_show() -> None:
    """Invariant: hidden money leaves the cost and gain cells empty."""
    row = lot_rows(_LOTS[1:2], MADRID, money=False)[0]
    assert (row["cost"], row["gain"]) == (None, None)


def test_lot_detail_lists_the_fifo_sale_with_its_figures() -> None:
    """Invariant: the detail shows proceeds, cost, fees, gain and the two-month rule of a sale."""
    text = _plain(lot_detail_text(_LOTS[1], MADRID, BASE, money=True))
    assert "proceeds              €80.00" in text
    assert "cost                  €100.00" in text
    assert "fees                  €0.20" in text
    assert f"gain                  {MINUS}€20.20" in text
    assert "two-month rule        flagged for your gestor" in text


def test_pack_text_says_it_is_never_submitted() -> None:
    """Invariant: the Tax pack sub-tab carries the line that nothing goes to AEAT."""
    text = _plain(pack_text(TaxPackStatus(year=2026, state="refreshed"), BASE))
    assert "Tax pack 2026 · refreshed" in text
    assert PACK_LINE in text


def test_pack_status_reads_the_markers(tmp_path: Path) -> None:
    """Invariant: no manifest is not built, a manifest is refreshed, the frozen marker is frozen."""
    assert pack_status(tmp_path, 2026, UTC).state == "not built yet"
    (tmp_path / "2026").mkdir()
    (tmp_path / "2026" / MANIFEST_FILE).write_text("{}", encoding="utf-8")
    assert pack_status(tmp_path, 2026, UTC).state == "refreshed"
    (tmp_path / "2026" / FROZEN_FILE).write_text("", encoding="utf-8")
    assert pack_status(tmp_path, 2026, UTC).state == "frozen"


_PROFILE = TaxpayerProfileView(exists=True, figures=(("general income", _D(31234)),))


def test_profile_is_masked_by_default() -> None:
    """Invariant: the profile's figures show as the mask until S is pressed."""
    text = _plain(profile_text(_PROFILE, BASE, shown=False))
    assert MASK in text
    assert "31,234" not in text


def test_profile_shows_figures_after_s() -> None:
    """Invariant: S reveals the figures on the PC."""
    assert "€31,234.00" in _plain(profile_text(_PROFILE, BASE, shown=True))


def test_remote_profile_says_only_whether_one_exists() -> None:
    """Invariant: over --vps the profile text names existence only, never a figure or mask."""
    remote = TaxpayerProfileView(exists=True, remote=True)
    text = _plain(profile_text(remote, BASE, shown=True))
    assert text.splitlines()[0] == "a taxpayer profile exists on this machine"
    assert MASK not in text
    assert "€" not in text


# SEV-1: S3 (taxpayer personal data must never leave the PC)
def test_profile_view_never_reads_the_file_over_vps(tmp_path: Path) -> None:
    """Invariant: over --vps the loader is never called, even when the file exists."""
    path = tmp_path / "taxpayer.yaml"
    path.write_text("x", encoding="utf-8")

    def boom(file: Path, year: int) -> dict[str, Decimal]:
        raise AssertionError(f"read {file} for {year}")

    view = profile_view(path, year=2026, remote=True, load=boom)
    assert view == TaxpayerProfileView(exists=True, remote=True)


def test_profile_view_reads_the_figures_on_the_pc(tmp_path: Path) -> None:
    """Invariant: on the PC the year's figures are read through the loader."""
    path = tmp_path / "taxpayer.yaml"
    path.write_text("x", encoding="utf-8")
    view = profile_view(
        path, year=2026, remote=False, load=lambda _f, _y: {"general income": _D(5)}
    )
    assert view.figures == (("general income", _D(5)),)


# SEV-1: S3 (taxpayer personal data must never leave the PC)
def test_tax_inputs_over_vps_never_read_the_profile(tmp_path: Path) -> None:
    """Invariant: with the remote marker set, a present profile is reported, never parsed."""
    profile = tmp_path / "taxpayer.yaml"
    profile.write_text("{not: [valid", encoding="utf-8")
    inputs = tax_inputs(
        demo=False,
        env={REMOTE_ENV: "1", "HERMES_TAXPAYER_PROFILE": str(profile)},
        db_path=tmp_path / "missing.sqlite3",
        config_dir=Path("config"),
        now=_FROZEN,
    )
    assert inputs.profile == TaxpayerProfileView(exists=True, remote=True)


def test_tax_inputs_on_the_pc_report_an_unreadable_profile(tmp_path: Path) -> None:
    """Invariant: on the PC the same file is read, so a broken one is reported by error type."""
    profile = tmp_path / "taxpayer.yaml"
    profile.write_text("{not: [valid", encoding="utf-8")
    inputs = tax_inputs(
        demo=False,
        env={"HERMES_TAXPAYER_PROFILE": str(profile)},
        db_path=tmp_path / "missing.sqlite3",
        config_dir=Path("config"),
        now=_FROZEN,
    )
    assert inputs.profile.error is not None
    assert inputs.profile.error.startswith("could not read it: ")
    assert inputs.source is None


# ---------------------------------------------------------------------------
# Pilot


def _app(source: _FakeTaxSource, profile: TaxpayerProfileView = _PROFILE) -> HomeApp:
    return HomeApp(
        FixtureReadModel.load("ok-two-positions"),
        clock=lambda: _FROZEN,
        motion=False,
        settings_path=None,
        theme=ThemeChoice.NEON,
        refresh_seconds=3600,
        tax=source,
        taxpayer_profile=profile,
        tax_pack=lambda year: TaxPackStatus(year=year, state="refreshed"),
    )


def _drive_pilot[T](
    keys: list[str],
    check: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    source: _FakeTaxSource | None = None,
    size: tuple[int, int] = (160, 45),
) -> T:
    async def run() -> T:
        app = _app(source or _FakeTaxSource(_known_screen()))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            return await check(app, pilot)

    return asyncio.run(run())


def _drive_async[T](
    keys: list[str],
    check: Callable[[HomeApp], Awaitable[T]],
    *,
    source: _FakeTaxSource | None = None,
    size: tuple[int, int] = (160, 45),
) -> T:
    async def with_pilot(app: HomeApp, pilot: Pilot[None]) -> T:
        del pilot
        return await check(app)

    return _drive_pilot(keys, with_pilot, source=source, size=size)


async def _until(done: Callable[[], bool], *, seconds: float = 5.0) -> None:
    """Wait until ``done()`` holds, so a slow runner cannot read a stale screen.

    Raises:
        TimeoutError: ``done()`` did not hold within ``seconds``.

    """
    async with asyncio.timeout(seconds):
        while not done():
            await asyncio.sleep(0.01)


def _drive[T](
    keys: list[str],
    check: Callable[[HomeApp], T],
    *,
    source: _FakeTaxSource | None = None,
    size: tuple[int, int] = (160, 45),
) -> T:
    async def wrapped(app: HomeApp) -> T:
        return check(app)

    return _drive_async(keys, wrapped, source=source, size=size)


def _tax(app: HomeApp) -> TaxTab:
    return app.query_one(TaxTab)


def test_pressing_4_shows_the_summary_from_the_source() -> None:
    """Invariant: tab 4 is the Tax screen, opening on Summary with the source's estimate."""

    def check(app: HomeApp) -> tuple[int, int, str]:
        tax = _tax(app)
        return app.tab, tax.sub, _plain(tax.texts["tax-summary"])

    tab, sub, text = _drive(["4"], check)
    assert (tab, sub) == (3, SUMMARY)
    assert "estimated tax (FIFO)    €123.45" in text


def test_the_source_gets_the_positions_booked_prices() -> None:
    """Invariant: "open if sold now" is asked at the booked prices of the open positions."""
    source = _FakeTaxSource(_known_screen())
    _drive(["4"], lambda app: None, source=source)
    assert set(source.prices[-1]) == {"ETH", "SOL"}


def test_brackets_cycle_the_sub_tabs() -> None:
    """Invariant: ] steps forward and [ steps back through the five sub-tabs, wrapping."""
    assert _drive(["4", "right_square_bracket"], lambda app: _tax(app).sub) == LOTS
    assert _drive(["4", "left_square_bracket"], lambda app: _tax(app).sub) == PROFILE


def test_brackets_do_nothing_off_the_tax_screen() -> None:
    """Invariant: [ and ] leave Home and the Tax sub-tab alone."""
    assert _drive(["right_square_bracket", "4"], lambda app: _tax(app).sub) == SUMMARY


def test_lots_show_every_lot_and_filter_by_state() -> None:
    """Invariant: the Lots list holds every lot; ``state:open`` keeps the two open ones."""

    async def check(app: HomeApp) -> tuple[int, list[str]]:
        query_list = app.query_one("#tax-lots", QueryList)
        total = len(query_list.shown)
        app.query_one(QueryInput).value = "state:open"
        await _until(lambda: len(query_list.shown) != total)
        return total, sorted(str(row["lot"]) for row in query_list.shown)

    total, kept = _drive_async(["4", "right_square_bracket"], check)
    assert total == 3
    assert kept == ["b-1", "b-3"]


def test_highlighting_a_lot_shows_its_fifo_detail() -> None:
    """Invariant: moving the table cursor onto the SOL lot shows the sale that used it."""

    async def check(app: HomeApp) -> str:
        table = app.query_one(SortableTable)
        table.focus()
        table.move_cursor(row=1)
        await _until(lambda: _plain(_tax(app).texts["tax-lot-detail"]).startswith("SOL lot b-2"))
        return _plain(_tax(app).texts["tax-lot-detail"])

    text = _drive_async(["4", "right_square_bracket"], check)
    assert text.startswith("SOL lot b-2 · closed")
    assert "two-month rule        flagged for your gestor" in text


def test_tab_in_the_query_completes_instead_of_switching_screens() -> None:
    """Invariant: Tab inside the Lots query completes a field name and keeps the Tax screen."""

    async def check(app: HomeApp, pilot: Pilot[None]) -> tuple[int, str]:
        field = app.query_one(QueryInput)
        field.focus()
        field.value = "sta"
        await pilot.pause()
        await app.run_action("cycle_tab(1)")
        return app.tab, field.value

    tab, value = _drive_pilot(["4", "right_square_bracket"], check)
    assert tab == 3
    assert value == "state:"


def test_s_shows_the_profile_and_s_again_masks_it() -> None:
    """Invariant: on Your profile S toggles the mask; the figures show only in between."""
    shown = _drive(
        ["4", "left_square_bracket", "s"], lambda app: _plain(_tax(app).texts["tax-profile"])
    )
    masked = _drive(
        ["4", "left_square_bracket", "s", "s"],
        lambda app: _plain(_tax(app).texts["tax-profile"]),
    )
    assert "€31,234.00" in shown
    assert MASK in masked


def test_s_off_the_profile_sub_tab_keeps_the_mask() -> None:
    """Invariant: S on another sub-tab never reveals the profile."""
    assert _drive(["4", "s"], lambda app: _tax(app).profile_shown) is False


def test_e_on_the_profile_says_editing_waits_for_mvp2() -> None:
    """Invariant: E on Your profile only shows the MVP2 notice; nothing is edited."""

    def check(app: HomeApp) -> list[str]:
        return [str(n.message) for n in app._notifications]

    assert EDIT_LATER in _drive(["4", "left_square_bracket", "e"], check)


def test_a_remote_console_shows_only_whether_a_profile_exists() -> None:
    """Invariant: over --vps the profile sub-tab names existence, even after S."""
    remote = TaxpayerProfileView(exists=False, remote=True)

    async def run() -> str:
        app = _app(_FakeTaxSource(_known_screen()), remote)
        async with app.run_test(size=(120, 36)) as pilot:
            for key in ("4", "left_square_bracket", "s"):
                await pilot.press(key)
                await pilot.pause()
            return _plain(_tax(app).texts["tax-profile"])

    text = asyncio.run(run())
    assert text.splitlines()[0] == "no taxpayer profile on this machine"
    assert "€" not in text


def test_the_pack_sub_tab_shows_the_pack_state() -> None:
    """Invariant: the Tax pack sub-tab shows the year's pack state and the AEAT line."""
    text = _drive(
        ["4", "left_square_bracket", "left_square_bracket"],
        lambda app: _plain(_tax(app).texts["tax-pack"]),
    )
    assert "Tax pack 2026 · refreshed" in text
    assert PACK_LINE in text


def test_the_tax_source_reads_the_ledger_from_the_console_worker_thread(tmp_path: Path) -> None:
    """The real Tax source, opened here, reads the 2026 screen from another thread (#1001).

    The console builds it on its UI thread and reads it in a worker; a
    same-thread-only connection would turn every read into "could not read
    the ledger".
    """
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        SqliteLedger(conn)
    inputs = tax_inputs(demo=False, env={}, db_path=db, config_dir=Path("config"), now=_FROZEN)
    source = inputs.source
    assert source is not None
    screens: list[object] = []
    worker = threading.Thread(
        target=lambda: screens.append(source.tax_screen(as_of=_FROZEN, prices={}))
    )
    worker.start()
    worker.join()
    inputs.close()
    assert [(type(screen).__name__, getattr(screen, "year", None)) for screen in screens] == [
        ("TaxScreen", 2026)
    ]
