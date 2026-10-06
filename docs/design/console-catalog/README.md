# Console catalog

One page with every reviewed picture of the console: each screen and state,
in Neon and Control room, at the design spec's four widths, all with
synthetic demo data. Use it to validate a console deploy: open the page for
the release, go through its "Validate this deploy" checklist, and compare
each changed view with the live console.

## Build it

```bash
python3 scripts/dev/console-catalog.py
```

The page lands in `build/console-catalog/index.html`. It names the release it
reflects (`git describe`) and lists the views whose snapshots changed since
the previous `v*` release tag; `--since <tag>` compares with another one.

## How it stays true

- The pictures are the SVG snapshots under
  `tests/unit/entrypoints/__snapshots__/`, which the console tests compare on
  every run, so the catalog shows exactly what this release draws.
- Each view has a caption in [`captions.toml`](captions.toml): the screen,
  the state, what it means, what to check, and how to open it with
  `hermes --demo <scenario> tui`.
- `tests/unit/scripts_dev/test_console_catalog.py` fails when a snapshot has
  no caption or a caption lacks a field. A PR that adds a snapshot adds its
  caption in the same PR; `python3 scripts/dev/console-catalog.py --check`
  says which one is missing.
