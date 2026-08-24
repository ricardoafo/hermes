"""Contract test — real Kraken ``/0/public/AssetPairs`` for BTC/USD.

Mirrors the contract-tier pattern for third-party endpoints (see
``docs/decisions/2026-08-23-contract-test-tier.md``). Kraken's public
endpoints require NO auth — they are always hittable, no secrets, no
skip-when-credentials-missing dance. The test is nevertheless gated by
the ``contract`` pytest marker so the per-PR unit path stays hermetic.

Rate-limited politely: one AssetPairs call is one token from Kraken's
1-call-per-second public tier — well inside every published limit.

Reference: docs/decisions/2026-08-24-kraken-public-adapter.md,
docs/decisions/2026-08-23-contract-test-tier.md.
"""

from __future__ import annotations

import httpx
import pytest

from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter
from hermes.domain.errors import KrakenPublicError
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


@pytest.mark.contract
def test_get_pair_fees_against_real_kraken_returns_populated_schedule() -> None:
    """Real Kraken AssetPairs for BTC/USDT returns a non-empty tiered schedule.

    Asserts:

    - The adapter constructs and closes cleanly against the live endpoint.
    - The response parses into at least one :class:`FeeTier` — Kraken has
      never published an empty schedule.
    - The lowest-threshold tier has positive maker AND taker bps —
      Kraken's standard tier is 16 bps maker / 26 bps taker at time of
      writing, so both non-zero is a reasonable long-lived assertion.

    This is the drift detector: if Kraken changes the response shape
    (removes ``fees_maker``, renames the tier array, etc.) this test
    goes red BEFORE the unit mocks lie about what production sees.
    """
    adapter = KrakenPublicAdapter.create()
    try:
        try:
            schedule = adapter.get_pair_fees(Pair(base=Currency.BTC, quote=Currency.USDT))
        except KrakenPublicError as exc:
            pytest.fail(
                f"Real Kraken AssetPairs failed for BTC/USDT: {exc}. "
                "If Kraken changed the endpoint or the pair altname, update the "
                "adapter's _PAIR_OVERRIDES table and re-run."
            )
        except httpx.HTTPError as exc:
            pytest.fail(f"Transport failure calling Kraken AssetPairs: {exc!r}")
        assert schedule.tiers, "Kraken returned an empty tier list — schema drift?"
        standard = schedule.tiers[0]
        assert standard.maker_bps > 0, (
            f"Kraken standard-tier maker bps was 0 — unexpected. Full tier: {standard!r}"
        )
        assert standard.taker_bps > 0, (
            f"Kraken standard-tier taker bps was 0 — unexpected. Full tier: {standard!r}"
        )
    finally:
        adapter.close()
