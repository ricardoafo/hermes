---
card_id: <strategy-id>
idea_family: <family, named by its entry signal>
batch: <the batch that will cite it>
status: DRAFT
---

# Card: <one line on the idea>

<!--
A hypothesis card (ADR-021 §1 stage 1, ADR-026 §1). Copy it to
docs/experiments/<experiment>/cards/<strategy-id>.md, fill every section and
commit it before the batch that cites it is signed. Its SHA-256 goes into the
batch's `card_sha256`. A card is never edited once a signed batch cites it; a
change is a new card.
-->

## Mechanism

<Why the market should pay this edge, and who is on the other side. Cite the source.>

## Rules

- Trend or regime filter: <...>
- Entry: <...>
- Exit, first to fire: <...>
- Sizing: <...>

## Free parameters (at most 3)

| Parameter | Base | Neighbours |
| --- | --- | --- |
| <name, what it sets> | <base> | <low>, <high> |

## Expected sign

- <What the statistics should show if the mechanism is real, such as win rate, trade count, bull and bear stretches.>

## Falsification test

Rejected if, on Train, <the check that would show the mechanism is not there>.
