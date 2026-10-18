- **Binance Vision historical klines downloader**
  (`scripts/data/ingest_binance_vision.py`,
  `openspec/changes/data-ingest-binance-vision/`): stdlib-only Python
  script that materialises the plan documented in
  `docs/experiments/swing-mvp1/data-ingest-plan.md` §1. Downloads all
  monthly klines ZIPs for BTC/ETH/SOL × 1h/4h/1d across the pre-reg
  v1 §4 uniform window (2020-09-01 → 2026-12-31; 684 target files).
  SHA256-verifies every ZIP against Binance's `.CHECKSUM` companion
  BEFORE writing to disk. Resume-safe: on rerun, files present on
  disk with a matching manifest entry are skipped; missing files are
  re-downloaded and re-verified. Manifest checkpointed every 20
  files so an interrupt loses at most 20 entries. CLI flags:
  `--data-dir`, `--manifest`, `--pair`, `--interval`, `--dry-run`.
  No new Python dependency (`urllib` + `hashlib` + `json` only).
  Operator runs locally on the dev machine; not a workflow — bulk
  download would burn CI runner time for zero value. Fingerprint
  output feeds pre-reg v1 §16 (Dataset fingerprints requirement).
