- **`hermes health` reads `.env.local`** (`src/hermes/entrypoints/cli.py`):
  probes now see credentials kept in `.env.local`, as `load_config` does; a
  missing file is a no-op and real environment variables still win.
  Closes #316.
