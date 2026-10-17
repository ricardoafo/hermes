"""The Tax screen of the Cockpit console (issue #719, design spec screen 4).

View only in MVP1. Five sub-tabs, switched with ``[`` and ``]`` or a click:

- **Summary**: realised gains, fees, taxable base, estimated tax (FIFO),
  open if sold now, the two-month-rule flags (traded coins only, never USDT)
  and the savings bracket meters with "€X left before 21%".
- **Lots**: every FIFO lot in the shared :class:`~hermes.entrypoints.tui_query.QueryList`
  (filter ``state:open``, ``coin:SOL``, free text; sort by a header or ``S``),
  month subtotals of what the filter keeps, and the highlighted lot's FIFO
  detail in a panel beside it (below it on a compact terminal).
- **Brackets**: every band of the savings scale as a meter.
- **Tax pack**: whether this year's pack is built, for the gestor; never
  submitted to AEAT.
- **Your profile**: masked; ``S`` shows the figures, ``E`` (edit) waits for
  MVP2. Over ``--vps`` the console never reads the profile and says only
  whether one exists.

Every figure comes from :class:`~hermes.application.tax.screen.TaxScreen`, built by
the tax service; this module only lays it out, and money shows only where
``pnl_visible`` allows it.

Reference: openspec/changes/719-tax-screen/change.md.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import ContentSwitcher, DataTable, Static

from hermes.adapters.console.tax_demo import DemoTaxSource, demo_profile
from hermes.adapters.ledger.connection import open_ledger
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.console.format import fmt_level, fmt_qty, round2, signed_eur
from hermes.application.console.query import Row, SortKey
from hermes.application.console.render import MADRID
from hermes.application.console.views import TaxEstimate, TaxPending
from hermes.application.filing.pack import FROZEN_FILE, MANIFEST_FILE
from hermes.application.tax.screen import (
    BracketFill,
    LotView,
    TaxpayerProfileView,
    TaxScreen,
    TaxScreenService,
    TaxScreenSource,
    month_subtotals,
)
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.tax.year_position import tax_year_of
from hermes.entrypoints.tax import to_domain_scales, to_domain_taxpayer
from hermes.entrypoints.tax_pack import DEFAULT_PACK_DIR, PACK_DIR_ENV
from hermes.entrypoints.tui_query import QueryList, SortableTable
from hermes.entrypoints.tui_theme import Tokens
from hermes.entrypoints.tui_widgets import BigNumber, HudPanel, Meter, Tab, TabBar
from hermes.entrypoints.vps import REMOTE_ENV
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.tax import (
    TaxpayerProfile,
    TaxScalesConfig,
    load_tax_scales,
    load_taxpayer_profile,
    resolve_taxpayer_profile_path,
)

__all__ = [
    "LOTS_PLACEHOLDER",
    "LOT_COLUMNS",
    "LOT_FIELDS",
    "MASK",
    "PACK_LINE",
    "SUBTABS",
    "TaxInputs",
    "TaxPackStatus",
    "TaxTab",
    "brackets_text",
    "lot_detail_text",
    "lot_rows",
    "pack_status",
    "pack_text",
    "profile_text",
    "profile_view",
    "subtotals_text",
    "summary_text",
    "tax_inputs",
]

SUBTABS: Final = (
    Tab("4.1", "Summary"),
    Tab("4.2", "Lots"),
    Tab("4.3", "Brackets"),
    Tab("4.4", "Tax pack"),
    Tab("4.5", "Your profile"),
)
SUMMARY: Final = 0
LOTS: Final = 1
BRACKETS: Final = 2
PACK: Final = 3
PROFILE: Final = 4
_PANES: Final = ("tax-summary-pane", "tax-lots-pane", "tax-brackets-pane", "tax-pack-pane")
_PANE_IDS: Final = (*_PANES, "tax-profile-pane")

PACK_LINE: Final = "for your gestor, never submitted to AEAT"
MASK: Final = "••••••"
HIDDEN: Final = "hidden"
TWO_MONTH_COINS: Final = "BTC, ETH and SOL only, never USDT"
EDIT_LATER: Final = "Editing the profile comes in MVP2 (it needs the Code Owner's OK)."
_FIGURES_STAY: Final = "taxpayer figures stay on your PC and never reach the VPS"
_PENDING_LOTS: Final = "No lots: the estimate is pending."
_NO_LOT: Final = "Highlight a lot to see the sales that used it."
_METER_CELLS: Final = 20
_DATE: Final = "%Y-%m-%d"

LOT_COLUMNS: Final = (
    ("when", "Bought"),
    ("coin", "Coin"),
    ("state", "State"),
    ("qty", "Qty"),
    ("cost", "Cost"),
    ("gain", "Gain"),
    ("flag", "2m"),
)
LOT_FIELDS: Final = ("year", "month", "day", "coin", "state", "lot", "qty", "cost", "gain")
_LOT_SORT: Final = (SortKey("when", descending=True),)
LOTS_PLACEHOLDER: Final = "filter: state:open coin:SOL month:2026-07 or any text · Tab completes"
_PROFILE_LABELS: Final = (
    "general income",
    "other savings income",
    "other capital gains",
    "personal minimum",
    "losses carried forward",
)


def _money(value: Decimal | None, *, money: bool) -> str:
    if not money:
        return HIDDEN
    return "pending" if value is None else fmt_level(value)


def _signed(value: Decimal | None, *, money: bool) -> str:
    if not money:
        return HIDDEN
    return "pending" if value is None else signed_eur(value)


def _tax(value: TaxEstimate | TaxPending, *, money: bool) -> str:
    if isinstance(value, TaxPending):
        return value.label
    return _money(value.amount, money=money)


def _gain_style(value: Decimal | None, tokens: Tokens) -> str:
    if value is None or value == 0:
        return tokens.text
    return f"bold {tokens.up}" if value > 0 else f"bold {tokens.down}"


def _row(label: str, value: str, tokens: Tokens, style: str | None = None) -> Text:
    return Text.assemble((f"{label:<24}", tokens.muted), (value, style or f"bold {tokens.text}"))


def _band_meter(fill: BracketFill, tokens: Tokens, *, width: int, money: bool) -> Text:
    rate = f"{fill.rate}%"
    if fill.upper is None:
        line = Text(f"{rate:>4} ", style=tokens.muted)
        line.append(f"above {_money(fill.lower, money=money)}", style=tokens.dim)
        if fill.filled > 0:
            line.append(f" · {_money(fill.filled, money=money)} in it", style=tokens.text)
        return line
    size = fill.upper - fill.lower
    meter = Meter(value=float(fill.filled), cap=float(size), width=width, warn_from=None)
    line = Text(f"{rate:>4} ", style=tokens.muted)
    line.append_text(meter.render(tokens))
    line.append(
        f" {_money(fill.filled, money=money)} of {_money(size, money=money)}", style=tokens.text
    )
    return line


def _room_line(screen: TaxScreen, tokens: Tokens, *, money: bool) -> Text:
    band = screen.estimate.band
    if band.next_rate is None or band.room_to_next is None:
        return Text(f"top band: {band.rate}% on every further euro", style=tokens.warn)
    left = _money(band.room_to_next, money=money)
    return Text.assemble(
        (left, f"bold {tokens.live}"),
        (f" left before {band.next_rate}%", tokens.text),
        (f" · now {band.rate}%", tokens.muted),
    )


def summary_text(
    screen: TaxScreen | TaxPending, tokens: Tokens, *, money: bool, width: int
) -> RenderableType:
    """Return the Summary sub-tab.

    Args:
        screen: The tax service's screen, or why there is none.
        tokens: The data token sheet of the current theme.
        money: Whether money may show (``pnl_visible``).
        width: Columns available.

    Returns:
        The figures, the two-month flags and the bracket meters.
    """
    if isinstance(screen, TaxPending):
        return Text(screen.label, style=tokens.warn)
    estimate = screen.estimate
    title = Text(f"Tax year {screen.year} · {estimate.label}", style=f"bold {tokens.text}")
    parts: list[RenderableType] = [
        title,
        Text(""),
        _row(
            "realised gains",
            _signed(estimate.realised_gain, money=money),
            tokens,
            _gain_style(estimate.realised_gain, tokens),
        ),
        _row("fees", _money(screen.fees, money=money), tokens),
        _row("taxable base", _money(estimate.savings_base, money=money), tokens),
        _row("estimated tax (FIFO)", _tax(estimate.hermes_tax, money=money), tokens),
        _row("open if sold now", _tax(estimate.tax_if_sold_now, money=money), tokens),
    ]
    parts.extend(
        _row(f"  {position.asset}", _tax(position.tax_if_sold_now, money=money), tokens)
        for position in estimate.per_position_if_sold_now
    )
    parts.extend(
        (
            _row("today's tax", _tax(estimate.today_tax, money=money), tokens),
            Text(""),
            Text(f"Two-month rule · {TWO_MONTH_COINS}", style=f"bold {tokens.text}"),
        )
    )
    if not estimate.two_month:
        parts.append(Text("no sale flagged", style=tokens.muted))
    parts.extend(
        Text.assemble(
            ("⚑ ", tokens.warn),
            (f"{flag.asset} ", f"bold {tokens.text}"),
            (f"loss {_money(-flag.loss, money=money)} on {flag.sold_on:{_DATE}}", tokens.text),
            (f", bought again {flag.bought_on:{_DATE}} · for your gestor", tokens.muted),
        )
        for flag in estimate.two_month
    )
    parts.extend((Text(""), Text("Savings brackets", style=f"bold {tokens.text}")))
    meter_width = max(6, min(_METER_CELLS, width - 40))
    reached = [fill for fill in screen.brackets if fill.filled > 0]
    shown = screen.brackets[: max(len(reached) + 1, 2)]
    parts.extend(_band_meter(fill, tokens, width=meter_width, money=money) for fill in shown)
    parts.append(_room_line(screen, tokens, money=money))
    parts.extend(Text(f"note: {notice}", style=tokens.dim) for notice in estimate.notices)
    return Group(*parts)


def brackets_text(
    screen: TaxScreen | TaxPending, tokens: Tokens, *, money: bool, width: int
) -> RenderableType:
    """Return the Brackets sub-tab: every savings band as a meter.

    Args:
        screen: The tax service's screen, or why there is none.
        tokens: The data token sheet.
        money: Whether money may show.
        width: Columns available.

    Returns:
        One meter per band, then the room left in the current one.
    """
    if isinstance(screen, TaxPending):
        return Text(screen.label, style=tokens.warn)
    meter_width = max(6, min(_METER_CELLS * 2, width - 40))
    parts: list[RenderableType] = [
        Text(
            f"Savings base {screen.year} · "
            f"{_money(screen.estimate.savings_base, money=money)} taxable",
            style=f"bold {tokens.text}",
        ),
        Text("the savings scale is national; you file as a Murcia resident", style=tokens.dim),
        Text(""),
    ]
    parts.extend(
        _band_meter(fill, tokens, width=meter_width, money=money) for fill in screen.brackets
    )
    parts.extend((Text(""), _room_line(screen, tokens, money=money)))
    return Group(*parts)


def _cents(value: Decimal | None) -> Decimal | None:
    return None if value is None else round2(value)


def lot_rows(lots: Sequence[LotView], tz: tzinfo, *, money: bool) -> list[Row]:
    """Return one query row per lot for the shared :class:`QueryList`.

    Args:
        lots: The lots.
        tz: Display timezone of the dates and the month facet.
        money: Whether money may show; hidden money leaves ``cost`` and ``gain`` empty.

    Returns:
        Rows with the shared facets (``year``, ``month``, ``day``, ``coin``,
        ``state``), the lot ref and the figures.
    """
    rows: list[Row] = []
    for lot in lots:
        local = lot.bought_at.astimezone(tz)
        gain = lot.gain if lot.sales else None
        rows.append(
            {
                "lot": lot.ref,
                "when": f"{local:{_DATE}}",
                "at": lot.bought_at,
                "year": local.year,
                "month": f"{local:%Y-%m}",
                "day": f"{local:{_DATE}}",
                "coin": lot.coin,
                "state": lot.state,
                "qty": lot.qty,
                "cost": _cents(lot.cost) if money else None,
                "gain": _cents(gain) if money else None,
                "flag": "⚑" if lot.two_month else "",
            }
        )
    return rows


def subtotals_text(
    lots: Sequence[LotView], tz: tzinfo, tokens: Tokens, *, money: bool
) -> RenderableType:
    """Return the month subtotals of the lots the filter keeps.

    Args:
        lots: The lots shown.
        tz: Display timezone.
        tokens: The data token sheet.
        money: Whether money may show.

    Returns:
        One line per month, newest first.
    """
    table = Table.grid(padding=(0, 2))
    for _ in range(4):
        table.add_column()
    for total in month_subtotals(lots, tz):
        table.add_row(
            Text(total.month, style=f"bold {tokens.text}"),
            Text(f"{total.lots} lot{'s' if total.lots != 1 else ''}", style=tokens.muted),
            Text(f"cost {_money(total.cost, money=money)}", style=tokens.text),
            Text(f"gain {_signed(total.gain, money=money)}", style=_gain_style(total.gain, tokens)),
        )
    return Group(Text("by month", style=tokens.muted), table)


def lot_detail_text(
    lot: LotView | None, tz: tzinfo, tokens: Tokens, *, money: bool
) -> RenderableType:
    """Return one lot's FIFO detail: the buy and every sale that used it.

    Args:
        lot: The highlighted lot, or ``None``.
        tz: Display timezone.
        tokens: The data token sheet.
        money: Whether money may show.

    Returns:
        The buy, then per sale: quantity, proceeds, cost, fees, gain and the
        two-month rule.
    """
    if lot is None:
        return Text(_NO_LOT, style=tokens.muted)
    bought = lot.bought_at.astimezone(tz)
    parts: list[RenderableType] = [
        Text.assemble(
            (f"{lot.coin} ", f"bold {tokens.text}"),
            (f"lot {lot.ref} · {lot.state}", tokens.muted),
        ),
        _row("bought", f"{fmt_qty(lot.qty)} on {bought:%Y-%m-%d %H:%M}", tokens),
        _row("cost, fee in", _money(lot.cost, money=money), tokens),
        _row("still held", fmt_qty(lot.remaining), tokens),
        Text(""),
        Text("FIFO sales that used it", style=f"bold {tokens.text}"),
    ]
    if not lot.sales:
        parts.append(Text("none yet", style=tokens.muted))
    for sale in lot.sales:
        sold = sale.at.astimezone(tz)
        parts.extend(
            (
                Text(
                    f"{sale.ref} · {sold:%Y-%m-%d %H:%M} · {fmt_qty(sale.qty)}", style=tokens.text
                ),
                _row("  proceeds", _money(sale.proceeds, money=money), tokens),
                _row("  cost", _money(sale.cost, money=money), tokens),
                _row("  fees", _money(sale.fee, money=money), tokens),
                _row(
                    "  gain",
                    _signed(sale.gain, money=money),
                    tokens,
                    _gain_style(sale.gain, tokens),
                ),
                _row(
                    "  two-month rule",
                    "flagged for your gestor" if sale.two_month else "not flagged",
                    tokens,
                    tokens.warn if sale.two_month else tokens.muted,
                ),
            )
        )
    return Group(*parts)


@dataclass(frozen=True, slots=True)
class TaxPackStatus:
    """Whether this year's tax pack is built.

    Attributes:
        year: Tax year.
        state: ``not built yet``, ``refreshed`` or ``frozen``.
        refreshed_at: When the manifest was last written, if built.
    """

    year: int
    state: str
    refreshed_at: datetime | None = None


def pack_status(root: Path, year: int, tz: tzinfo) -> TaxPackStatus:
    """Return the pack's state from its folder, reading no file content.

    Args:
        root: The packs folder (``hermes tax-pack --out``).
        year: Tax year.
        tz: Timezone of the refresh time.

    Returns:
        ``frozen`` when the frozen marker exists, ``refreshed`` when a
        manifest exists, else ``not built yet``.
    """
    folder = root / str(year)
    manifest = folder / MANIFEST_FILE
    if not manifest.is_file():
        return TaxPackStatus(year=year, state="not built yet")
    at = datetime.fromtimestamp(manifest.stat().st_mtime, tz)
    state = "frozen" if (folder / FROZEN_FILE).is_file() else "refreshed"
    return TaxPackStatus(year=year, state=state, refreshed_at=at)


def pack_text(status: TaxPackStatus | None, tokens: Tokens) -> RenderableType:
    """Return the Tax pack sub-tab.

    Args:
        status: The pack's state, or ``None`` when it cannot be read here.
        tokens: The data token sheet.

    Returns:
        The state and the line that it is never submitted.
    """
    if status is None:
        head = Text("Tax pack · not available here", style=tokens.muted)
    else:
        head = Text.assemble(
            (f"Tax pack {status.year} · ", f"bold {tokens.text}"),
            (status.state, f"bold {tokens.live}"),
        )
        if status.refreshed_at is not None:
            head.append(f" · refreshed {status.refreshed_at:%Y-%m-%d %H:%M}", style=tokens.muted)
    return Group(
        head,
        Text(PACK_LINE, style=tokens.text),
        Text(""),
        Text("build or refresh it with: hermes tax-pack", style=tokens.dim),
    )


def profile_text(
    profile: TaxpayerProfileView | None, tokens: Tokens, *, shown: bool
) -> RenderableType:
    """Return the Your profile sub-tab.

    Args:
        profile: What may be said about the profile; ``None`` when unknown.
        tokens: The data token sheet.
        shown: ``S`` was pressed: show the figures instead of the mask.

    Returns:
        Over ``--vps`` only whether a profile exists; on the PC the figures,
        masked unless ``shown``.
    """
    if profile is None:
        return Text("Profile · not available here", style=tokens.muted)
    exists = "a taxpayer profile exists" if profile.exists else "no taxpayer profile"
    if profile.remote:
        return Group(
            Text(f"{exists} on this machine", style=f"bold {tokens.text}"),
            Text(_FIGURES_STAY, style=tokens.muted),
        )
    parts: list[RenderableType] = [Text(exists, style=f"bold {tokens.text}")]
    if profile.error is not None:
        parts.append(Text(profile.error, style=tokens.warn))
    for label, value in profile.figures:
        parts.append(_row(label, fmt_level(value) if shown else MASK, tokens))
    hint = "S hides" if shown else "S shows"
    parts.extend(
        (
            Text(""),
            Text(f"{hint} · E edit (MVP2) · {_FIGURES_STAY}", style=tokens.dim),
        )
    )
    return Group(*parts)


def profile_view(
    path: Path,
    *,
    year: int,
    remote: bool,
    load: Callable[[Path, int], Mapping[str, Decimal]],
) -> TaxpayerProfileView:
    """Return what the Tax screen may say about the profile at ``path``.

    Args:
        path: The profile file (``~/.config/hermes/taxpayer.yaml``).
        year: Tax year whose figures to show.
        remote: The console runs over ``--vps``: ``load`` is never called.
        load: Reads the year's figures from the file, ``label -> EUR``;
            raises ``ValueError`` on an unreadable file.

    Returns:
        The view; over ``--vps`` only whether the file exists.
    """
    exists = path.is_file()
    if remote or not exists:
        return TaxpayerProfileView(exists=exists, remote=remote)
    try:
        figures = load(path, year)
    except ValueError as exc:
        return TaxpayerProfileView(exists=True, error=f"could not read it: {type(exc).__name__}")
    return TaxpayerProfileView(exists=True, figures=tuple(figures.items()))


class TaxTab(Container):
    """The Tax screen: sub-tab bar and the five sub-tabs.

    The app calls :meth:`show` on every redraw while the screen is up; the
    widget keeps the highlighted lot, the sub-tab and whether the profile is
    shown.
    """

    DEFAULT_CSS = """
    TaxTab { height: 1fr; background: $hud-void; }
    TaxTab > #tax-subtabs { height: 1; }
    TaxTab > #tax-panes { height: 1fr; }
    TaxTab #tax-lots-pane { layout: vertical; height: 1fr; overflow-y: auto; }
    TaxTab.-beside #tax-lots-pane { layout: horizontal; overflow-y: hidden; }
    TaxTab #tax-lots-panel { height: auto; }
    TaxTab #tax-lots-panel > QueryList { height: 16; }
    TaxTab.-beside #tax-lots-panel { height: 1fr; }
    TaxTab.-beside #tax-lots-panel > QueryList { height: 1fr; }
    TaxTab #tax-detail-panel { height: auto; }
    TaxTab.-beside #tax-lots-panel { width: 3fr; }
    TaxTab.-beside #tax-detail-panel { width: 2fr; height: 1fr; max-height: 100%; margin-left: 1; }
    TaxTab.-beside #tax-lot-detail { height: 1fr; }
    """
    BESIDE_FROM: ClassVar[int] = 100

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        motion: bool,
        id: str | None = None,
    ) -> None:
        """Remember the clock the query list's ``since:`` reads.

        Args:
            now: The app's clock.
            motion: Count the big figure up when it changes.
            id: Widget id.
        """
        super().__init__(id=id)
        self._now = now
        self._motion = motion
        self.sub = SUMMARY
        self.profile_shown = False
        self._lots: dict[str, LotView] = {}
        self._lot_rows: tuple[Row, ...] = ()
        self.selected: str | None = None
        self._tz: tzinfo | None = None
        self._tokens: Tokens | None = None
        self._money = True
        self.texts: dict[str, RenderableType] = {}

    def compose(self) -> ComposeResult:
        """Lay out the sub-tab bar and the panes."""
        yield TabBar(SUBTABS, id="tax-subtabs")
        with ContentSwitcher(initial=_PANE_IDS[SUMMARY], id="tax-panes"):
            yield VerticalScroll(
                HudPanel(
                    BigNumber(motion=self._motion, id="tax-big"),
                    Static(id="tax-summary"),
                    title="Summary",
                    subtitle="[ ] sub-tabs",
                ),
                id="tax-summary-pane",
            )
            with Container(id="tax-lots-pane"):
                yield HudPanel(
                    QueryList(
                        (),
                        LOT_COLUMNS,
                        fields=LOT_FIELDS,
                        facets=("coin", "state", "month"),
                        sort=_LOT_SORT,
                        now=self._now,
                        placeholder=LOTS_PLACEHOLDER,
                        id="tax-lots",
                    ),
                    Static(id="tax-subtotals"),
                    title="Lots",
                    subtitle="state:open coin:SOL · ↵ table · S sort",
                    id="tax-lots-panel",
                )
                yield HudPanel(
                    Static(id="tax-lot-detail"),
                    title="Lot detail",
                    subtitle="FIFO",
                    id="tax-detail-panel",
                )
            yield VerticalScroll(
                HudPanel(Static(id="tax-brackets"), title="Brackets"), id="tax-brackets-pane"
            )
            yield VerticalScroll(
                HudPanel(Static(id="tax-pack"), title="Tax pack"), id="tax-pack-pane"
            )
            yield VerticalScroll(
                HudPanel(
                    Static(id="tax-profile"), title="Your profile", subtitle="S show · E edit"
                ),
                id="tax-profile-pane",
            )

    def _set(self, widget_id: str, content: RenderableType) -> None:
        self.texts[widget_id] = content
        self.query_one(f"#{widget_id}", Static).update(content)

    def show(
        self,
        screen: TaxScreen | TaxPending,
        *,
        profile: TaxpayerProfileView | None,
        pack: TaxPackStatus | None,
        tokens: Tokens,
        tz: tzinfo,
        money: bool,
        width: int,
        big_number: bool,
    ) -> None:
        """Draw every sub-tab from one read of the tax service.

        Args:
            screen: The tax service's screen, or why there is none.
            profile: What may be said about the profile.
            pack: The tax pack's state.
            tokens: The data token sheet.
            tz: Display timezone.
            money: Whether money may show (``pnl_visible``).
            width: Columns available.
            big_number: Room for the big estimated-tax figure.
        """
        self._tz, self._tokens, self._money = tz, tokens, money
        self.set_class(width >= self.BESIDE_FROM, "-beside")
        self.query_one("#tax-subtabs", TabBar).active = self.sub
        inner = width - 4
        self._set("tax-summary", summary_text(screen, tokens, money=money, width=inner))
        self._set("tax-brackets", brackets_text(screen, tokens, money=money, width=inner))
        self._set("tax-pack", pack_text(pack, tokens))
        self._set("tax-profile", profile_text(profile, tokens, shown=self.profile_shown))
        big = self.query_one("#tax-big", BigNumber)
        amount = None
        if isinstance(screen, TaxScreen) and isinstance(screen.estimate.hermes_tax, TaxEstimate):
            amount = screen.estimate.hermes_tax.amount
        if big_number and money and amount is not None:
            big.show(fmt_level(amount), float(amount))
        else:
            big.show(None, None)
        lots = screen.lots if isinstance(screen, TaxScreen) else ()
        self._lots = {lot.ref: lot for lot in lots}
        rows = tuple(lot_rows(lots, tz, money=money))
        query_list = self.query_one("#tax-lots", QueryList)
        if rows != self._lot_rows:
            self._lot_rows = rows
            query_list.set_rows(rows)
        self._draw_lots()

    def _draw_lots(self) -> None:
        tokens, tz = self._tokens, self._tz
        if tokens is None or tz is None:
            return
        query_list = self.query_one("#tax-lots", QueryList)
        shown = [self._lots[str(row["lot"])] for row in query_list.shown]
        if not self._lots:
            self._set("tax-subtotals", Text(_PENDING_LOTS, style=tokens.muted))
        else:
            self._set("tax-subtotals", subtotals_text(shown, tz, tokens, money=self._money))
        if self.selected not in self._lots and query_list.shown:
            self.selected = str(query_list.shown[0]["lot"])
        lot = self._lots.get(self.selected) if self.selected is not None else None
        self._set("tax-lot-detail", lot_detail_text(lot, tz, tokens, money=self._money))

    def switch(self, step: int) -> None:
        """Show the next (``1``) or previous (``-1``) sub-tab.

        Args:
            step: Direction.
        """
        self.pick((self.sub + step) % len(SUBTABS))

    def pick(self, index: int) -> None:
        """Show sub-tab ``index``.

        Args:
            index: ``0`` Summary to ``4`` Your profile.
        """
        self.sub = index
        self.query_one("#tax-panes", ContentSwitcher).current = _PANE_IDS[index]
        self.query_one("#tax-subtabs", TabBar).active = index
        if index != LOTS:
            self.app.set_focus(None)

    def on_tab_bar_picked(self, message: TabBar.Picked) -> None:
        """A click on a sub-tab; kept from the screen tabs above."""
        message.stop()
        self.pick(message.index)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Show the highlighted lot's FIFO detail."""
        if not isinstance(event.data_table, SortableTable):
            return
        shown = self.query_one("#tax-lots", QueryList).shown
        row = event.coordinate.row
        if 0 <= row < len(shown):
            self.selected = str(shown[row]["lot"])
        self._draw_lots()

    def on_query_list_refreshed(self, event: QueryList.Refreshed) -> None:
        """Re-total the months and keep the detail after the list re-filtered or re-sorted."""
        event.stop()
        self._draw_lots()

    def toggle_profile(self) -> None:
        """``S`` on Your profile: show or mask the figures."""
        self.profile_shown = not self.profile_shown


@dataclass(frozen=True, slots=True)
class TaxInputs:
    """What ``hermes tui`` hands the Tax screen.

    Attributes:
        source: The screen's source, or ``None`` when the scales cannot load.
        profile: What the screen may say about the taxpayer profile.
        pack: Returns the year's tax pack state.
        close: Releases the ledger connection the source holds.
    """

    source: TaxScreenSource | None
    profile: TaxpayerProfileView
    pack: Callable[[int], TaxPackStatus | None]
    close: Callable[[], None]


def _profile_figures(profile: TaxpayerProfile, year: int) -> dict[str, Decimal]:
    if not profile.has_year(year):
        return {}
    figures = profile.for_year(year)
    out = {
        _PROFILE_LABELS[0]: figures.general_income_eur,
        _PROFILE_LABELS[1]: figures.other_savings_income_eur,
        _PROFILE_LABELS[2]: figures.other_capital_gains_eur,
    }
    if figures.personal_minimum_eur is not None:
        out[_PROFILE_LABELS[3]] = figures.personal_minimum_eur
    carried = [loss.amount_eur for loss in figures.losses_carried_forward]
    if carried:
        out[_PROFILE_LABELS[4]] = sum(carried, start=Decimal(0))
    return out


def _load_profile(path: Path) -> TaxpayerProfile:
    try:
        return load_taxpayer_profile(path)
    except ConfigError as exc:
        raise ValueError(type(exc).__name__) from None


def tax_inputs(
    *,
    demo: bool,
    env: Mapping[str, str],
    db_path: Path,
    config_dir: Path,
    now: datetime,
) -> TaxInputs:
    """Build the Tax screen's inputs for one ``hermes tui`` run.

    Under ``--demo`` every figure is synthetic. Otherwise the source reads the
    ledger read-only through the tax service. Over ``--vps`` (``REMOTE_ENV``
    set) the taxpayer profile is never read: the estimate assumes no profile
    and the screen says only whether a profile file exists.

    Args:
        demo: A demo scenario is shown.
        env: Environment snapshot.
        db_path: The ledger file.
        config_dir: Where ``tax_scales.yaml`` lives.
        now: The read time; picks the profile's tax year.

    Returns:
        The inputs; a missing scale file leaves ``source`` ``None``.
    """
    try:
        scales_config: TaxScalesConfig | None = load_tax_scales(config_dir)
    except ConfigError:
        scales_config = None
    scales = {} if scales_config is None else to_domain_scales(scales_config)
    minimum = Decimal(0) if scales_config is None else scales_config.personal_minimum_eur
    if demo:
        return TaxInputs(
            source=DemoTaxSource(scales=scales, default_personal_minimum=minimum),
            profile=demo_profile(),
            pack=lambda year: TaxPackStatus(year=year, state="not built in the demo"),
            close=lambda: None,
        )
    remote = env.get(REMOTE_ENV) == "1"
    year = tax_year_of(now)
    path = resolve_taxpayer_profile_path(dict(env))
    loaded: list[TaxpayerProfile] = []

    def figures(file: Path, for_year: int) -> dict[str, Decimal]:
        loaded.append(_load_profile(file))
        return _profile_figures(loaded[0], for_year)

    profile = profile_view(path, year=year, remote=remote, load=figures)
    taxpayer: dict[int, TaxpayerYear] = {}
    if loaded and scales_config is not None:
        mapped = to_domain_taxpayer(loaded[0], year, default_personal_minimum=minimum)
        if mapped is not None:
            taxpayer[year] = mapped
    pack_root = Path(env.get(PACK_DIR_ENV, "").strip() or DEFAULT_PACK_DIR).expanduser()
    tz = MADRID

    def pack(for_year: int) -> TaxPackStatus | None:
        return pack_status(pack_root, for_year, tz)

    if scales_config is None or not db_path.is_file():
        return TaxInputs(source=None, profile=profile, pack=pack, close=lambda: None)
    connection = open_ledger(db_path, read_only=True)
    source = TaxScreenService(
        SqliteLedger(connection),
        scales=scales,
        taxpayer=taxpayer,
        default_personal_minimum=minimum,
    )
    return TaxInputs(source=source, profile=profile, pack=pack, close=connection.close)
