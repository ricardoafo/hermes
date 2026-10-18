# Change: 733-image

**SDD change**: `733-image`
**Date**: 2026-10-05
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/733-image/change`.

## Summary

Adds the `textual-image` dependency (pinned `>=0.14.1,<0.15`) and one
real use of it: a reusable `Logo` widget that shows the "Winged H" brand
mark where the terminal can draw images and a block-letter HERMES
wordmark everywhere else. The Code Owner approved the library on
2026-10-05 in the console design's "needs your OK" list and chose the
Winged H logo the same day. The widget is not wired into start-up; that is
issue #721. Main risk: `textual-image` is LGPL-3.0-or-later, which the
licence allowlist does not list (see Risks).

## Scope

### In

- `textual-image` in `pyproject.toml` and `uv.lock` (via `uv add`).
- `src/hermes/entrypoints/tui_logo.py`: `Logo`, `wordmark()`,
  `terminal_shows_images()`, `wordmark_text()`: the wordmark takes its colours
  from the theme tokens (Neon `brand` gradient, Control room plain `live` cyan).
- The packaged picture `src/hermes/entrypoints/assets/winged_h.png`, drawn by
  `scripts/assets/make_winged_h_logo.py`.

### Out

- Showing the logo at start-up: issue #721.

## Decisions

### D-1: Decide from the environment, never by probing

- **Decision**: `terminal_shows_images(env, is_tty=...)` is a pure function over
  named terminals; ssh sessions and non-ttys get the wordmark.
- **Alternatives**: let `textual_image` auto-detect: it queries the terminal on
  import, which hangs or prints escape codes in tests, pipes and some ssh paths.
- **Rationale**: a wrong guess prints garbage; a wordmark is always correct.
- **Cost of reversal**: LOW.

### D-2: Import `textual_image` lazily

- **Decision**: import it inside `Logo.compose`, only on the image path.
- **Rationale**: its import runs a terminal cell-size query.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Dependency is pinned and locked

- **A-1.1**: `rg 'textual-image>=0.14.1,<0.15' pyproject.toml`
- **A-1.2**: `python scripts/dev/reproducibility-check.py` exits 0.

### R-2: Wordmark fallback

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_tui_logo.py` passes.

### R-3: Packaged picture

- **A-3.1**: `src/hermes/entrypoints/assets/winged_h.png` loads as a 192x96 RGBA image.

## Design

`Logo(use_image=False)` yields a `Static` wordmark by default; with
`use_image=True` it yields `textual_image.widget.Image(LOGO_PATH)` and falls
back to the wordmark if the import fails. Callers pass
`terminal_shows_images(os.environ, is_tty=sys.stdout.isatty())`.

## Test plan

1. `uv run pytest tests/unit/entrypoints/test_tui_logo.py`.
2. `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit, property

## Risks

- **Licence**: `textual-image` is LGPL-3.0-or-later; `scripts/dev/license-check.py`
  allowlists permissive licences only and `known_licenses.yaml` has no exception
  for it. The check already reports other LGPL packages (`python-telegram-bot`,
  `fpdf2`) on main, so this adds one more to a known state. It is imported
  unmodified as a library, which LGPL allows. Pillow (MIT-CMU) is already locked
  through `fpdf2`.
- **Picture on an unlisted terminal**: shows the wordmark, never garbage.

## Dependencies on other changes

- Issue #721 wires the widget into start-up.
