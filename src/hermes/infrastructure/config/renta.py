"""Renta Web campaign layouts: ``config/renta_campaigns.yaml``.

Reference: openspec/changes/568-tax-pack/change.md D-6.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hermes.domain.filing.renta_campaign import CampaignLayout
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_yaml_model

__all__ = ["CampaignConfig", "RentaCampaignsConfig", "load_renta_campaigns"]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class CampaignConfig(BaseModel):
    """One campaign's boxes.

    Attributes:
        transmissions_box: Box of the virtual-currency transmissions entries.
        source: Where the box was checked.
        verified_on: When it was checked.
    """

    model_config = _MODEL_CONFIG

    transmissions_box: str = Field(pattern=r"^[0-9]{4}$")
    source: str = Field(min_length=1)
    verified_on: date


class RentaCampaignsConfig(BaseModel):
    """Contents of ``config/renta_campaigns.yaml``."""

    model_config = _MODEL_CONFIG

    campaigns: dict[int, CampaignConfig]

    def layouts(self) -> tuple[CampaignLayout, ...]:
        """Return the domain layouts, by tax year."""
        return tuple(
            CampaignLayout(
                tax_year=year,
                transmissions_box=campaign.transmissions_box,
                source=campaign.source,
                verified_on=campaign.verified_on,
            )
            for year, campaign in sorted(self.campaigns.items())
        )


def load_renta_campaigns(
    config_dir: Path | None = None, *, env: dict[str, str] | None = None
) -> RentaCampaignsConfig:
    """Load ``renta_campaigns.yaml`` from ``config_dir``.

    Args:
        config_dir: Directory holding the YAML files; defaults to ``./config``.
        env: Optional env snapshot for ``HERMES_RENTA__...`` overrides.

    Returns:
        The validated config.

    Raises:
        ConfigError: File missing, invalid YAML, or schema violation.
    """
    base = config_dir if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_yaml_model(
        base / "renta_campaigns.yaml", RentaCampaignsConfig, env_section="RENTA", env=env
    )
