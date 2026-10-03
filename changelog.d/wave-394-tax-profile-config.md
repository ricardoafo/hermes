- **Tax config** (`config/tax_scales.yaml`, `hermes.infrastructure.config.tax`):
  per-year IRPF savings scale (2025 and 2026, 30% top band) with a fail-closed loader,
  plus a local taxpayer profile (`~/.config/hermes/taxpayer.yaml`, override
  `HERMES_TAXPAYER_PROFILE`) whose values never appear in repr, logs or errors.
  Closes #394.
