# Notification UX research (2026-10-04)

**Status**: Research digest for issue #144 (research phase). Feeds the later
`docs/decisions/...-notification-templates-v2.md`; it does not decide
anything. The design choice belongs to the Code Owner.
**Companion**: [Notification inventory](2026-10-04-notification-inventory.md)
describes what exists today.
**Method**: Web search plus fetches of primary documentation (platform docs,
vendor help pages, one SRE book chapter, one clinical alarm study). Claims
marked "hypothesis" are inferences, not findings. Sources are listed at the
end; summaries are in my own words.

## Question

What makes a trading or operations notification something a solo operator
reads in under two seconds on a phone, trusts, and does not mute, given that
hermes pushes no outcome figure before certification (#319)?

## Findings

### A. How trading and alert tools structure alerts

The tools studied are a broker's mobile app, a charting platform's alert
system, and, for ops, an SRE paging model. They agree on a small set of
structural choices.

- **An alert is a rule plus a delivery plan, not just a message.** The
  charting platform's alert is made of a trigger condition, a frequency
  (fire once, or on every occurrence), an expiry timer, a name, a custom
  message with placeholders, and a set of channels (in-app push, pop-up,
  email, webhook, sound). The message body is whatever the user templated;
  the platform adds no wrapper. Takeaway for hermes: frequency and expiry are
  first-class, so "once per bar close" or "once per state change" should be
  explicit properties of each event class.
- **Alerts are named by what happened to an instrument.** The broker's mobile
  alerts come in three kinds (price, trade, margin), each scoped by
  instrument and, for trades, by where the order came from. Triggered alerts
  stay visible in a list marked as triggered, so the notification is a
  pointer and the app is the record. Takeaway: one message per kind, with the
  record living elsewhere (hermes: ledger, Slack, `hermes logs`).
- **The useful payload is small.** For strategy alerts, the platform's
  documented placeholder set centers on instrument, action, quantity,
  position size, price and time; community guides add time to measure latency
  and price to measure slippage. That is the order, side, size, price, time
  core that a fill message needs.
- **Delivery is per-channel, with the phone push as the thinnest one.** The
  broker's own guidance is to make push banners persistent with previews
  shown, which tells us that for trades the lock-screen preview is the
  product. Anything not visible in the first line is effectively behind a
  tap.
- **Page only for actionable symptoms.** The SRE chapter says every page
  should be actionable, should demand judgment rather than a rote step, and
  should describe a symptom instead of a cause; it asks of each alert whether
  it can be ignored as benign, whether it indicates real harm, and whether
  someone else is already paged for it. Takeaway: a page a script could
  answer is a log line.

What the sources do not give: I found no public description of how a broker
formats the body of a fill push, so the shape below is inferred from the
placeholder lists and general UI guidance (hypothesis).

### B. Telegram and Slack constraints and guidance

Confirmed platform facts that bound every template:

| Fact | Telegram | Slack |
| --- | --- | --- |
| Text size | 1 to 4096 characters after entity parsing | 4,000 recommended for `text`; section text 3,000; header 150 plain text only; 50 blocks per message |
| Formatting | `MarkdownV2`, `HTML`, or entities; MarkdownV2 requires escaping reserved characters | `mrkdwn` in sections, `plain_text` in headers |
| Quiet delivery | `disable_notification` sends with no sound | no per-message flag; quietness comes from channel notification settings |
| Rate | about 1 message per second per chat; 20 per minute in a group | about 1 message per second per channel, with burst allowance |
| Fallback | not applicable | `text` is the notification preview and screen-reader content when blocks exist |
| Threading | reply-to message | `thread_ts` posts as a reply |

Guidance from the Slack developer docs, paraphrased: batch high-volume
notifications into digests instead of firing each one, keep too many blocks
out of a message, put all essential content in the top-level text field so
accessibility tools and push previews have it, and treat `@channel` as
near-forbidden. Block Kit's context block exists to hold secondary details
under the primary content.

Consequences for hermes:

- A Slack push preview shows the `text` fallback, not the header block. The
  current fallback strings are already one line with the key facts; a v2
  template should keep that discipline and put the decision-relevant words
  first.
- Slack header blocks take `plain_text` only and have no color setting. Color
  on Slack needs a different carrier (hypothesis: attachment color bar or a
  leading glyph in the section). Needs a check in a real workspace.
- Telegram's 4096 limit is far above the 150-character scan target, so
  brevity is a reading-time choice, not a platform constraint.
- Silent versus loud exists only on Telegram. ADR-015 D-06 and D-07 already
  assume it; the templated path does not use it yet.

### C. Information hierarchy and alert fatigue

- **Fatigue is measured, and it is about trust.** In ICU monitoring,
  false-alarm shares of 86 to 99.5 percent are reported; in a nurse survey,
  98 percent agreed that nuisance alarms reduce trust and lead people to turn
  alarms off, and only about half said lethal alarms get immediate action.
  The mitigations are prioritization by severity and source, per-patient
  tuning, and removing alarms that do not change what anyone does. The
  hospital rule that followed (a 2014 patient safety goal) asks facilities to
  rank which alarm signals matter most. Takeaway: every extra message class
  taxes the trust in the one that matters.
- **Unranked alerts defeat the display.** The usability group's guidance is
  to direct attention to critical values without flashing endless alerts; its
  notification advice is to alert only when needed, at the moment the user can
  act, and to retire an alert once the issue is resolved. Takeaway: change-only
  alerts and a closing "recovered" message are the cleanest expression of that
  rule (ADR-015 D-07 already does this).
- **Dashboard design for glances.** The standard text on at-a-glance
  monitoring argues for everything fitting one screen, for size, color
  intensity and position to create a reading order, and for compact marks
  (sparklines, bullet graphs) instead of dials. In a text notification the
  equivalents are line order, bold or caps for one anchor, and no more than
  one deviation highlighted.
- **Numbers.** Fintech typography guidance converges on locale-correct
  separators from the platform formatter, right-aligned tabular figures in
  lists, and one dominant figure per view. Plain-text notifications cannot
  guarantee tabular figures, so alignment comes from fixed field order and
  fixed precision per field.
- **Color is never the only carrier.** The accessibility criterion on use of
  color says color cannot be the sole way to convey meaning; roughly 8 percent
  of men have a color vision deficiency, and red/green pairs are the common
  failure. Hermes's red, yellow, blue circles pass only because each message
  also has a word title; success (green) versus failure (red) must also carry
  a word or a shape.
- **Design-system severity vocabulary.** A widely used design system maps
  informational to blue, success to green, warning to yellow, error to red,
  each with an icon, and says system-generated messages need more context than
  task-triggered ones because the user did not just do something. Hermes
  messages are all system-generated, which argues for naming the strategy,
  pair and reason in the message itself.

### D. Design principles distilled for hermes

Each principle carries its source finding and the hermes constraint it serves.

1. **First line decides.** The Telegram first line and the Slack `text`
   fallback must carry class, subject and the one number that matters, because
   that is the lock-screen preview (A, B). Target under 2 seconds to scan;
   the 150-character target in `telegram.py` is a good proxy.
2. **One message, one decision or one fact.** If the operator cannot do
   anything, it goes to Slack, the ledger or `hermes logs`, not the phone
   (SRE actionability, C). Hermes is a solo operator; no one else gets paged.
3. **Change-only, and always close the loop.** Alert on state change; send a
   short recovery for every pause or down (C, ADR-015 D-07). Closes the gap
   that `StrategyResumed` has no Telegram template.
4. **Tiered loudness.** Down, kill trigger, auth failure: loud. Signals and
   recoveries: silent push. Heartbeat OK: nothing. Reuse `disable_notification`
   and the quiet-hours rule in ADR-015 D-08 (B).
5. **Blind by construction.** Pushed messages show plan, not outcome, until
   certification: pair, side, entry, stop, size, time, never P&L (#319). A
   plan-versus-actual shape must render the actual column as withheld, not
   blank, so the absence is legible. This is the main hermes-specific
   departure from broker alerts, which lead with P&L.
6. **EUR for money, native units for the trade.** Show the instrument's own
   price and size in its quote unit, and money amounts in EUR with the quote
   currency shown once if it differs (the ledger stores EUR values, #435).
   Hypothesis: dual-currency lines are worth it only for balances and
   notional, not for every price.
7. **Madrid time for the human, UTC for the machine.** Show a Madrid clock
   time with its zone label in pushed text and keep ISO UTC in the context
   line or the ledger. The daily line already does both. Durations as
   `15 min`, not `900s`.
8. **Fixed field order and precision per field.** Same order in every message
   of a family; thousands separator and decimal count fixed per field, using
   one formatter rather than per-template f-strings (C).
9. **Color plus word.** A severity glyph and a severity word on both
   channels, with the same mapping everywhere including the shell scripts
   (C). Keep the glyph set to three or four.
10. **Keep the fallback tier plain.** Shell alerts that must work without the
    venv stay plain text but follow the same title, number and ref order, so a
    failure at 03:00 reads like the rest (hypothesis).
11. **Use threads for detail, not duplicates.** Slack posts the same facts
    plus detail as a reply or an expandable block, rather than a second copy
    (B, Slack batching guidance).

## Candidate template shapes

Three shapes per event family. Descriptions only; no template text is
committed. `Telegram` and `Slack` below refer to channel pairs when both fire.

### Family 1: trade lifecycle (signal, entry, stop change, exit, rejection)

| Shape | Description | Tradeoffs |
| --- | --- | --- |
| Compact | Three lines. Line 1: severity glyph, kind and instrument ("Entry long BTC/USDT"). Line 2: price, stop, size in fixed order, Madrid time. Line 3: strategy and ref. Slack mirrors it with a context block holding UTC and ref. | Scans fast and fits the lock screen; no plan-versus-actual; carries stop but not rationale. |
| Verbose | Compact plus a plan block (entry, stop, target, size, risk in EUR) and, after certification, an actual block (fill, slippage, result). Pre-certification the actual block reads "withheld". Slack puts the block in a thread reply. | Best audit trail and matches the swing plan-versus-actual ask; longer to read; the withheld block is dead weight for months. |
| Minimal | One line: "Long BTC/USDT 61,234 stop 59,000 (18:00)". No ref on Telegram. Slack keeps the ref in a context block. | Fastest; hard to grep journald without the ref; loses strategy id and sizing. |

### Family 2: health and ops (pause, resume, anomaly, balance, crash, heartbeat)

| Shape | Description | Tradeoffs |
| --- | --- | --- |
| Compact | Two to three lines: glyph and word ("Down", "Late", "Paused"), the subject, since when in Madrid time plus elapsed, one action hint. Recovery uses the same layout with "Back after 12 min". | Matches ADR-015 change-only alerts; needs elapsed-time arithmetic in the template. |
| Verbose | Compact plus the last journal lines or error count, the reason string and the restart count. Slack gets this; Telegram does not. | Good for diagnosis from a desk; on phone it is text the operator will not read at 03:00. |
| Minimal | Single word and subject: "DOWN hermes.service 03:12". Recovery: "OK hermes.service 03:24 (12 min)". | Cheapest to read and to write in bash, so shell scripts can share it; no hint, no reason, so a tap into Slack is required. |

### Family 3: reports (daily digest, weekly rebalance, monthly tax, rebalance heads-up)

| Shape | Description | Tradeoffs |
| --- | --- | --- |
| Compact | Slack summary block: a headline sentence with the count of events and the one flag (anything off), then three labelled numbers; CSV attached in a thread. Telegram sends nothing. | One daily glance on Slack; hides detail behind the attachment. |
| Verbose | Slack headline plus sections per stream, a table of positions and weights, rebalance proposals with before and after weights, EUR totals. Telegram gets a one-line pointer at report time. | Complete; longest to build and the one most at risk of leaking an outcome word pre-certification. |
| Minimal | The daily alive line only, extended with the day's signal and rebalance counts. | Smallest surface, nothing new to certify; says nothing about content. |

## Recommendation

For discussion; the Code Owner decides.

- **Trade lifecycle: compact on Telegram, verbose (plan block in a thread) on
  Slack.** The phone gets the decision facts in the first line and the stop
  that defines the plan; Slack is the durable record. The actual block lands
  only when certified.
- **Health and ops: compact on both channels, with the minimal layout reused
  for the shell scripts.** Compact gives the elapsed time and action hint that
  the change-only model needs; the minimal layout is the fallback that bash can
  reproduce.
- **Reports: compact on Slack, one-line pointer on Telegram only for the
  weekly rebalance heads-up.** A compact summary with the CSV in a thread keeps
  the report scannable and the blind rule easy to enforce; add the verbose
  per-stream sections after certification.

Open for the Code Owner: whether Slack gets glyph-based color, whether the
daily line joins the template system, and where the Madrid-time and EUR
formatters live so every channel and the scripts share one rule.

## What this closes

- Platform limits and the loud-versus-silent mechanics for both channels.
- A source-backed list of principles and three shapes per family to bring to
  the templates-v2 decision.

## What this opens

- How the broker and charting apps format the body of a trade push (not
  documented publicly; needs hands-on observation of a real app).
- Whether Slack color is achievable with Block Kit alone (check in the real
  workspace).
- How a plan-versus-actual message looks pre-certification when the actual
  half must be withheld.
- Whether EUR everywhere reads better than EUR plus native units for fills.

## Sources

- [Telegram Bot API reference](https://core.telegram.org/bots/api): message
  text limit of 1 to 4096 characters after entity parsing, parse modes,
  `disable_notification`.
- [Telegram bots FAQ](https://core.telegram.org/bots/faq): per-chat and
  per-group send limits.
- [Slack header block reference](https://docs.slack.dev/reference/block-kit/blocks/header-block):
  150-character plain-text limit.
- [Slack chat.postMessage reference](https://docs.slack.dev/reference/methods/chat.postMessage):
  `text` as notification fallback, 4,000-character guidance, `thread_ts`,
  per-channel rate.
- [Designing with Block Kit](https://docs.slack.dev/concepts/designing-with-block-kit/):
  context blocks, progressive disclosure.
- [Slack marketplace best practices](https://slack.dev/marketplace-best-practices/):
  digests over single messages, accessible top-level text, `@channel` use.
- [Alert fatigue in user interfaces (video page)](https://www.nngroup.com/videos/alert-fatigue-user-interfaces/)
  and [Designing useful smart-home notifications](https://www.nngroup.com/articles/smart-home-notifications/):
  prioritize attention, notify only when needed, expire resolved alerts.
- [Monitoring distributed systems, SRE book](https://sre.google/sre-book/monitoring-distributed-systems/):
  actionable pages, symptoms over causes, signal-to-noise.
- [Nurses' perceptions of clinical alarms (JMIR Human Factors, 2015)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4797660/):
  false-alarm range, trust erosion, mitigations.
- [Reducing the harm associated with clinical alarm systems](https://dspace.lib.hawaii.edu/items/35a0232d-5b3d-4f4d-9046-2e75f6392763/full)
  and [Diagnosing and treating alarm fatigue](https://www.jointcommissionjournal.com/article/S1553-7250(16)42039-8/abstract):
  the 2014 alarm safety goal and its critique.
- [TradingView: how to set up alerts](https://www.tradingview.com/support/solutions/43000595315-how-to-set-up-alerts/):
  condition, frequency, expiry, message, channels.
- [IBKR Mobile: set alerts](https://www.ibkrguides.com/iphone/alerts.htm) and
  [IBKR Mobile notification FAQ](https://www.interactivebrokers.com/lib/cstools/faq/content/1163263595):
  price, trade and margin alert kinds; push settings.
- [Carbon design system: notifications](https://www.carbondesignsystem.com/building-blocks/core/patterns/notifications):
  severity colors and icons, toast versus inline, content writing.
- [Information Dashboard Design (publisher listing)](https://dl.acm.org/doi/abs/10.5555/1206491):
  at-a-glance monitoring, visual hierarchy, sparklines and bullet graphs.
- [Making color usage accessible (Section508.gov)](https://www.section508.gov/create/making-color-usage-accessible/)
  and [WCAG 1.4.1 explainer](https://wcag.dock.codes/documentation/wcag141/):
  color not the sole carrier; color-vision-deficiency prevalence.
- [The elements of fintech typography, part 1](https://medium.com/design-bootcamp/the-elements-of-fintech-typography-part-1-readable-money-b6c1226acbde)
  and [Decimal alignment on numbers](https://adrianroselli.com/2013/05/my-kingdom-for-decimal-alignment-on.html):
  tabular figures, locale-aware formatting, alignment (secondary sources).
