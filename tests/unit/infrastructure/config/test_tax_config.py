"""Tax config: savings scales per year and the private taxpayer profile.

Invariants protected (ADR-016 decisions 1 and 12):

- the repo scale is data, fails closed on a missing year, and has the right 2025/2026 bands;
- the profile is optional, local, validated, and its values never leak through
  repr, str, error messages or log output.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.infrastructure.config import (
    NO_PROFILE_NOTICE,
    ConfigError,
    TaxpayerProfile,
    load_tax_scales,
    load_taxpayer_profile,
)
from hermes.infrastructure.config.tax import (
    SavingsScale,
    TaxScalesConfig,
    resolve_taxpayer_profile_path,
)

_REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"

# Distinctive figures so a leak is unmistakable in any captured text.
_SECRET_PROFILE = dedent(
    """
    years:
      2026:
        general_income_eur: "48123.45"
        personal_minimum_eur: "5551.11"
        other_savings_income_eur: "7777.77"
        other_capital_gains_eur: "3141.59"
        losses_carried_forward:
          - { origin_year: 2024, amount_eur: "2718.28" }
    """
)
_SECRET_FRAGMENTS = ("48123", "5551.11", "7777.77", "3141.59", "2718.28")


def _write(tmp_path: Path, text: str, name: str = "taxpayer.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Savings scales
# ---------------------------------------------------------------------------


def test_repo_scale_has_adr_016_bands_for_2025_and_2026() -> None:
    """ADR-016 item 1: 2025 and 2026 are 19/21/23/27/30% with the stated limits."""
    cfg = load_tax_scales(_REPO_CONFIG, env={})
    for year in (2025, 2026):
        bands = [(b.up_to_eur, b.rate) for b in cfg.savings_scale_for(year).bands]
        assert bands == [
            (Decimal("6000"), Decimal("0.19")),
            (Decimal("50000"), Decimal("0.21")),
            (Decimal("200000"), Decimal("0.23")),
            (Decimal("300000"), Decimal("0.27")),
            (None, Decimal("0.30")),
        ]


def test_repo_scale_default_personal_minimum_is_5550() -> None:
    """The shipped default personal minimum is 5,550 EUR."""
    assert load_tax_scales(_REPO_CONFIG, env={}).personal_minimum_eur == Decimal("5550")


def test_missing_year_fails_closed_with_year_in_message() -> None:
    """A year with no scale raises 'scale missing for <year>' instead of guessing."""
    cfg = load_tax_scales(_REPO_CONFIG, env={})
    with pytest.raises(ConfigError, match=r"scale missing for 2027"):
        cfg.savings_scale_for(2027)


def test_missing_scales_file_raises_config_error(tmp_path: Path) -> None:
    """No tax_scales.yaml means a ConfigError, never default rates."""
    with pytest.raises(ConfigError, match="not found"):
        load_tax_scales(tmp_path, env={})


@pytest.mark.parametrize(
    "bands",
    [
        '[{ up_to_eur: "6000", rate: "0.19" }]',
        '[{ up_to_eur: "50000", rate: "0.21" }, { up_to_eur: "6000", rate: "0.19" },'
        ' { rate: "0.3" }]',
        '[{ rate: "0.19" }, { up_to_eur: "6000", rate: "0.21" }]',
        '[{ up_to_eur: "6000", rate: "1.5" }, { rate: "0.3" }]',
        "[]",
    ],
)
def test_malformed_bands_are_rejected(tmp_path: Path, bands: str) -> None:
    """Closed top band, non-ascending limits, rates above 100% and empty scales all fail load."""
    _write(
        tmp_path,
        f'personal_minimum_eur: "5550"\nsavings:\n  2026:\n    bands: {bands}\n',
        "tax_scales.yaml",
    )
    with pytest.raises(ConfigError, match="validation failed"):
        load_tax_scales(tmp_path, env={})


@given(
    limits=st.lists(st.integers(min_value=1, max_value=10**7), min_size=1, max_size=6, unique=True)
)
def test_any_strictly_ascending_limits_with_open_top_are_accepted(limits: list[int]) -> None:
    """Property: sorted unique limits plus an open top band always validate and round-trip."""
    ordered = sorted(limits)
    bands = [{"up_to_eur": str(v), "rate": "0.20"} for v in ordered] + [{"rate": "0.30"}]
    scale = SavingsScale.model_validate({"bands": bands})
    assert [b.up_to_eur for b in scale.bands[:-1]] == [Decimal(v) for v in ordered]
    assert scale.bands[-1].up_to_eur is None


def test_scales_config_requires_at_least_one_year() -> None:
    """An empty savings table is invalid, not 'no scales needed'."""
    with pytest.raises(ValueError, match="savings"):
        TaxScalesConfig.model_validate({"personal_minimum_eur": "5550", "savings": {}})


# ---------------------------------------------------------------------------
# General-base scales (total IRPF view, change total-irpf-view)
# ---------------------------------------------------------------------------


def _quotas_at_limits(scale: SavingsScale) -> list[Decimal]:
    """Cumulative quota at each bounded band limit, as the official tables print it."""
    quotas: list[Decimal] = []
    total, lower = Decimal(0), Decimal(0)
    for band in scale.bands:
        if band.up_to_eur is None:
            break
        total += (band.up_to_eur - lower) * band.rate
        quotas.append(total)
        lower = band.up_to_eur
    return quotas


@pytest.mark.parametrize("year", [2025, 2026])
def test_repo_state_scale_reproduces_the_published_quotas(year: int) -> None:
    """Ley 35/2006 art. 63.1.1: the published cuota integra at each limit.

    1,182.75 / 2,112.75 / 4,362.75 / 8,950.75 / 62,950.75 at
    12,450 / 20,200 / 35,200 / 60,000 / 300,000, then 24.5%.
    """
    state, _ = load_tax_scales(_REPO_CONFIG, env={}).general_scales_for("murcia", year)
    limits = ("12450", "20200", "35200", "60000", "300000")
    assert [b.up_to_eur for b in state.bands] == [*(Decimal(v) for v in limits), None]
    quotas = ("1182.75", "2112.75", "4362.75", "8950.75", "62950.75")
    assert _quotas_at_limits(state) == [Decimal(v) for v in quotas]
    assert state.bands[-1].rate == Decimal("0.245")


@pytest.mark.parametrize("year", [2025, 2026])
def test_repo_murcia_scale_reproduces_the_published_quotas(year: int) -> None:
    """Decreto Legislativo 1/2010 (Murcia) art. 2: the published cuota integra at each limit.

    1,182.75 / 2,050.75 / 3,886.15 / 8,540.15 at 12,450 / 20,200 / 34,000 / 60,000,
    then 22.5%.
    """
    _, murcia = load_tax_scales(_REPO_CONFIG, env={}).general_scales_for("murcia", year)
    limits = ("12450", "20200", "34000", "60000")
    assert [b.up_to_eur for b in murcia.bands] == [*(Decimal(v) for v in limits), None]
    quotas = ("1182.75", "2050.75", "3886.15", "8540.15")
    assert _quotas_at_limits(murcia) == [Decimal(v) for v in quotas]
    assert murcia.bands[-1].rate == Decimal("0.225")


@pytest.mark.parametrize(
    ("region", "year", "message"),
    [
        ("murcia", 2027, "no scale for state 2027"),
        ("madrid", 2026, "no scale for madrid 2026"),
    ],
)
def test_missing_general_scale_fails_closed(region: str, year: int, message: str) -> None:
    """A region or year without a table is an error naming it, never a borrowed scale."""
    cfg = load_tax_scales(_REPO_CONFIG, env={})
    with pytest.raises(ConfigError, match=message):
        cfg.general_scales_for(region, year)


def test_missing_regional_year_names_the_region() -> None:
    """With the state scale present, a missing regional year names the region and year."""
    cfg = TaxScalesConfig.model_validate(
        {
            "personal_minimum_eur": "5550",
            "savings": {2026: {"bands": [{"rate": "0.19"}]}},
            "general": {
                "state": {2027: {"bands": [{"rate": "0.2"}]}},
                "regions": {"murcia": {2026: {"bands": [{"rate": "0.2"}]}}},
            },
        }
    )
    with pytest.raises(ConfigError, match="no scale for murcia 2027"):
        cfg.general_scales_for("murcia", 2027)


def test_scales_file_without_general_section_still_loads() -> None:
    """The general tables are optional; without them every lookup fails closed."""
    cfg = TaxScalesConfig.model_validate(
        {"personal_minimum_eur": "5550", "savings": {2026: {"bands": [{"rate": "0.19"}]}}}
    )
    with pytest.raises(ConfigError, match="no scale for state 2026"):
        cfg.general_scales_for("murcia", 2026)


def test_unknown_key_in_general_section_is_rejected(tmp_path: Path) -> None:
    """extra="forbid": a typo such as `region:` instead of `regions:` fails load."""
    _write(
        tmp_path,
        'personal_minimum_eur: "5550"\nsavings:\n  2026:\n    bands: [{ rate: "0.19" }]\n'
        "general:\n  region: {}\n",
        "tax_scales.yaml",
    )
    with pytest.raises(ConfigError, match="validation failed"):
        load_tax_scales(tmp_path, env={})


# ---------------------------------------------------------------------------
# Taxpayer profile: loading
# ---------------------------------------------------------------------------


def test_missing_profile_file_yields_zeros_and_notice(tmp_path: Path) -> None:
    """No profile: all-zero year profile and the 'no taxpayer profile' notice."""
    profile = load_taxpayer_profile(tmp_path / "absent.yaml")
    year = profile.for_year(2026)
    assert year.general_income_eur == Decimal("0")
    assert year.other_savings_income_eur == Decimal("0")
    assert year.other_capital_gains_eur == Decimal("0")
    assert year.personal_minimum_eur is None
    assert year.losses_carried_forward == ()
    assert profile.notice_for(2026) == "no taxpayer profile, assuming no other income"


def test_profile_year_present_has_no_notice_and_exact_values(tmp_path: Path) -> None:
    """A configured year returns its figures and carries no notice."""
    profile = load_taxpayer_profile(_write(tmp_path, _SECRET_PROFILE))
    year = profile.for_year(2026)
    assert year.general_income_eur == Decimal("48123.45")
    assert year.personal_minimum_eur == Decimal("5551.11")
    assert year.other_savings_income_eur == Decimal("7777.77")
    assert year.other_capital_gains_eur == Decimal("3141.59")
    assert [(c.origin_year, c.amount_eur) for c in year.losses_carried_forward] == [
        (2024, Decimal("2718.28"))
    ]
    assert profile.notice_for(2026) is None


def test_year_absent_from_profile_falls_back_to_zeros_with_notice(tmp_path: Path) -> None:
    """A year the operator did not fill in is assumed zero and says so."""
    profile = load_taxpayer_profile(_write(tmp_path, _SECRET_PROFILE))
    assert profile.for_year(2025).general_income_eur == Decimal("0")
    assert profile.notice_for(2025) == NO_PROFILE_NOTICE


def test_committed_example_loads_with_zero_income() -> None:
    """The committed example is valid and holds no real income figures."""
    profile = load_taxpayer_profile(_REPO_CONFIG / "taxpayer.example.yaml")
    year = profile.for_year(2026)
    assert profile.has_year(2026)
    assert (
        year.general_income_eur,
        year.other_savings_income_eur,
        year.other_capital_gains_eur,
        year.losses_carried_forward,
    ) == (Decimal("0"), Decimal("0"), Decimal("0"), ())


def test_env_var_overrides_default_profile_path(tmp_path: Path) -> None:
    """HERMES_TAXPAYER_PROFILE wins over the home default."""
    target = tmp_path / "custom.yaml"
    assert resolve_taxpayer_profile_path({"HERMES_TAXPAYER_PROFILE": str(target)}) == target


def test_default_profile_path_is_under_home_config_hermes() -> None:
    """Without the env var the path is ~/.config/hermes/taxpayer.yaml, expanded."""
    resolved = resolve_taxpayer_profile_path({})
    assert resolved == Path.home() / ".config" / "hermes" / "taxpayer.yaml"


def test_empty_profile_file_is_treated_as_no_profile(tmp_path: Path) -> None:
    """An empty file behaves like a missing one: zeros plus notice."""
    profile = load_taxpayer_profile(_write(tmp_path, ""))
    assert profile.notice_for(2026) == NO_PROFILE_NOTICE


# ---------------------------------------------------------------------------
# Taxpayer profile: bad values fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        'general_income_eur: "-1"',
        'general_income_eur: "abc"',
        'other_savings_income_eur: "NaN"',
        'other_capital_gains_eur: "Infinity"',
        'personal_minimum_eur: "-5"',
        "unknown_field: 1",
        "losses_carried_forward: [{ origin_year: 1850, amount_eur: '10' }]",
        "losses_carried_forward: [{ origin_year: 2024, amount_eur: '-10' }]",
    ],
)
def test_bad_profile_values_raise_config_error(tmp_path: Path, body: str) -> None:
    """Negative, non-numeric, non-finite or unknown fields fail closed."""
    path = _write(tmp_path, f"years:\n  2026:\n    {body}\n")
    with pytest.raises(ConfigError, match="invalid taxpayer profile"):
        load_taxpayer_profile(path)


def test_invalid_yaml_and_non_mapping_raise_config_error(tmp_path: Path) -> None:
    """Unparseable YAML and a non-mapping root both fail closed."""
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_taxpayer_profile(_write(tmp_path, "years: [unclosed", "a.yaml"))
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_taxpayer_profile(_write(tmp_path, "- 1\n- 2\n", "b.yaml"))


# ---------------------------------------------------------------------------
# Taxpayer profile: privacy (ADR-016 decision 12)
# ---------------------------------------------------------------------------


def test_profile_values_never_appear_in_repr_or_str(tmp_path: Path) -> None:
    """Repr and str of the profile and its year/loss models expose no figures."""
    profile = load_taxpayer_profile(_write(tmp_path, _SECRET_PROFILE))
    year = profile.for_year(2026)
    rendered = " ".join(
        [
            repr(profile),
            str(profile),
            repr(year),
            str(year),
            repr(year.losses_carried_forward),
            repr(year.losses_carried_forward[0]),
        ]
    )
    for fragment in _SECRET_FRAGMENTS:
        assert fragment not in rendered


def test_profile_values_never_appear_in_log_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Loading a profile (and reading it back) logs no profile figures."""
    with caplog.at_level(logging.DEBUG):
        profile = load_taxpayer_profile(_write(tmp_path, _SECRET_PROFILE))
        profile.for_year(2026)
        load_taxpayer_profile(tmp_path / "absent.yaml")
    assert caplog.records, "loader should log something so this test can fail"
    for fragment in _SECRET_FRAGMENTS:
        assert fragment not in caplog.text


def test_profile_values_never_appear_in_error_messages(tmp_path: Path) -> None:
    """Validation errors name fields but never echo the offending figures."""
    secret = "9999123.45"  # noqa: S105 - fake figure, not a credential
    path = _write(
        tmp_path,
        f'years:\n  2026:\n    general_income_eur: "{secret}"\n'
        f'    other_savings_income_eur: "-{secret}"\n',
    )
    with pytest.raises(ConfigError) as excinfo:
        load_taxpayer_profile(path)
    assert secret not in str(excinfo.value)
    assert "other_savings_income_eur" in str(excinfo.value)


def test_empty_profile_object_is_default_constructible() -> None:
    """TaxpayerProfile() is the 'no profile' value used when nothing is configured."""
    assert TaxpayerProfile().notice_for(2026) == NO_PROFILE_NOTICE


def test_other_capital_gains_may_be_a_net_loss(tmp_path: Path) -> None:
    """A same-year loss outside hermes loads as a negative amount, since it nets in-year."""
    profile = load_taxpayer_profile(
        _write(tmp_path, 'years:\n  2026:\n    other_capital_gains_eur: "-1200.50"\n')
    )
    assert profile.for_year(2026).other_capital_gains_eur == Decimal("-1200.50")


def test_total_irpf_view_is_off_by_default(tmp_path: Path) -> None:
    """A profile that does not mention the view leaves it off, with no region."""
    profile = load_taxpayer_profile(_write(tmp_path, _SECRET_PROFILE))
    assert (profile.show_total_irpf, profile.region) == (False, None)


def test_view_fields_load_and_deductions_are_per_year(tmp_path: Path) -> None:
    """Region and show_total_irpf are top level; general_deductions_eur is per year."""
    body = (
        "show_total_irpf: true\nregion: murcia\n"
        'years:\n  2026:\n    general_income_eur: "40000"\n    general_deductions_eur: "2500"\n'
    )
    profile = load_taxpayer_profile(_write(tmp_path, body))
    assert (profile.show_total_irpf, profile.region) == (True, "murcia")
    assert profile.for_year(2026).general_deductions_eur == Decimal("2500")


@pytest.mark.parametrize(
    "body",
    [
        'years:\n  2026:\n    general_deductions_eur: "-1"\n',
        "region: Murcia Region\n",
        "show_total_irpf: maybe\n",
    ],
)
def test_bad_view_fields_raise_config_error(tmp_path: Path, body: str) -> None:
    """Negative deductions, a non-slug region and a non-boolean switch all fail closed."""
    with pytest.raises(ConfigError, match="invalid taxpayer profile"):
        load_taxpayer_profile(_write(tmp_path, body))


def test_region_and_deductions_never_appear_in_repr_errors_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The region and deductions are private like every other profile value (ADR-016 item 12)."""
    caplog.set_level(logging.DEBUG)
    good = load_taxpayer_profile(
        _write(tmp_path, 'region: murcia\nyears:\n  2026:\n    general_deductions_eur: "9876.54"\n')
    )
    assert "murcia" not in repr(good)
    assert "9876" not in repr(good.for_year(2026))
    with pytest.raises(ConfigError) as caught:
        load_taxpayer_profile(_write(tmp_path, "region: Secret Town 42\n", "bad.yaml"))
    assert "Secret Town" not in str(caught.value)
    assert "murcia" not in caplog.text
    assert "Secret Town" not in caplog.text
