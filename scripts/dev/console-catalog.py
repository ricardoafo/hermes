#!/usr/bin/env python3
"""Build the console design catalog from the TUI snapshot SVGs (#851).

Every reviewed SVG snapshot of the console becomes an example in one HTML
page, grouped by screen, with the caption from
``docs/design/console-catalog/captions.toml`` saying what the state means,
what to check and how to open it with demo data. The page names the release
it reflects and starts with a "validate this deploy" checklist: the views
whose snapshots changed since the previous release tag.

The pictures are the test snapshots themselves, so the catalog cannot drift
from the code; ``--check`` (and a unit test) fail when a snapshot has no
caption.

Usage:
    python3 scripts/dev/console-catalog.py                 # build/console-catalog/index.html
    python3 scripts/dev/console-catalog.py --check         # captions cover every snapshot?
    python3 scripts/dev/console-catalog.py --since v1.0.0-shadow.1 --out /tmp/catalog.html

Read-only on the repo. Needs ``git`` only for the release name and the
checklist; without it the page says so.
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = ROOT / "tests" / "unit" / "entrypoints" / "__snapshots__"
CAPTIONS = ROOT / "docs" / "design" / "console-catalog" / "captions.toml"
DEFAULT_OUT = ROOT / "build" / "console-catalog" / "index.html"
SCREENS = (
    "Home",
    "Signals",
    "Positions",
    "Tax",
    "Shadow test",
    "Briefs",
    "System",
    "Logs",
    "Help",
    "Theme picker",
    "Drill-down",
    "Parts",
)
"""Screen order on the page: the tab order, then overlays and shared parts."""
CAPTION_FIELDS = ("screen", "state", "means", "check")
THEMES = ("neon", "control", "night")
_SIZE = re.compile(r"(?:_(?P<a>\d+x\d+)|\[(?P<b>\d+x\d+)\])$")
_RELEASE_TAG = "v*"


@dataclass(frozen=True)
class Shot:
    """One snapshot SVG.

    Attributes:
        path: The file.
        view: ``<test module>/<view>``, the caption key.
        theme: ``neon``, ``control`` or ``night``; empty for the default theme.
        size: ``<cols>x<rows>``; empty when the name carries none.
    """

    path: Path
    view: str
    theme: str
    size: str


def parse_shot(path: Path, root: Path = SNAPSHOTS) -> Shot:
    """Return the view, theme and size a snapshot file name encodes.

    Names are ``<view>_<theme>_<cols>x<rows>.svg`` or, for older tests,
    ``<test>_<cols>x<rows>.svg`` and ``<test>[<cols>x<rows>].svg``.

    Args:
        path: The SVG file.
        root: The snapshot directory the view key is relative to.

    Returns:
        The parsed shot.
    """
    name = path.stem
    size = ""
    if match := _SIZE.search(name):
        size = match.group("a") or match.group("b")
        name = name[: match.start()]
    theme = ""
    for candidate in THEMES:
        if name.endswith(f"_{candidate}"):
            theme, name = candidate, name.removesuffix(f"_{candidate}")
            break
    module = path.parent.relative_to(root).as_posix()
    return Shot(path=path, view=f"{module}/{name}", theme=theme, size=size)


def find_shots(root: Path = SNAPSHOTS) -> list[Shot]:
    """Return every SVG snapshot under ``root``, sorted by path."""
    return [parse_shot(path, root) for path in sorted(root.rglob("*.svg"))]


def load_captions(path: Path = CAPTIONS) -> dict[str, dict[str, str]]:
    """Return the captions file as ``{view: {field: text}}``."""
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    views = raw.get("views", {})
    return {
        str(view): {str(k): str(v) for k, v in fields.items()} for view, fields in views.items()
    }


def problems(shots: Iterable[Shot], captions: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Return what keeps the catalog from covering every snapshot.

    Args:
        shots: The snapshots found.
        captions: The captions by view.

    Returns:
        One line per view without a caption, per caption missing a field, and
        per unknown screen name; empty when the catalog is complete.
    """
    found: list[str] = []
    for view in sorted({shot.view for shot in shots}):
        caption = captions.get(view)
        if caption is None:
            found.append(f"{view}: no caption in {CAPTIONS.relative_to(ROOT)}")
            continue
        found.extend(
            f"{view}: caption has no {field!r}"
            for field in CAPTION_FIELDS
            if not caption.get(field, "").strip()
        )
        if caption.get("screen") and caption["screen"] not in SCREENS:
            found.append(f"{view}: unknown screen {caption['screen']!r} (one of {SCREENS})")
    return found


def changed_views(changed_paths: Iterable[str], root: Path = SNAPSHOTS) -> list[str]:
    """Return the views whose snapshot files are among ``changed_paths`` (repo-relative)."""
    prefix = root.relative_to(ROOT).as_posix() + "/"
    views = {
        parse_shot(ROOT / path, root).view
        for path in changed_paths
        if path.startswith(prefix) and path.endswith(".svg")
    }
    return sorted(views)


def _git(*args: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv; git resolved via shutil.which
            [git, *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
        )
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.strip()


def previous_release() -> str | None:
    """Return the newest release tag that is not on HEAD itself, or ``None``."""
    head = _git("rev-parse", "HEAD")
    tags = _git("tag", "--merged", "HEAD", "--list", _RELEASE_TAG, "--sort=-creatordate")
    for tag in (tags or "").splitlines():
        if _git("rev-list", "-n", "1", tag) != head:
            return tag
    return None


def demo_command(caption: Mapping[str, str]) -> str:
    """Return how to open the state on a console, from the caption's scenario and keys."""
    if caption.get("demo"):
        return caption["demo"]
    scenario = caption.get("scenario", "ok-two-positions")
    keys = caption.get("keys", "").strip()
    command = f"hermes --demo {scenario} tui"
    return f"{command}, then press {keys}" if keys else command


def _size_key(shot: Shot) -> tuple[int, int]:
    cols, _, rows = shot.size.partition("x")
    return (int(cols), int(rows)) if cols.isdigit() and rows.isdigit() else (0, 0)


def _img(shot: Shot) -> str:
    data = base64.b64encode(shot.path.read_bytes()).decode("ascii")
    label = " · ".join(part for part in (shot.theme or "default theme", shot.size) if part)
    return (
        f'<figure><img loading="lazy" alt="{html.escape(shot.view)} {html.escape(label)}" '
        f'src="data:image/svg+xml;base64,{data}"><figcaption>{html.escape(label)}</figcaption>'
        "</figure>"
    )


_STYLE = """
:root { color-scheme: light dark; --bg: #faf9f5; --ink: #141413; --muted: #5d6780;
  --line: #d9d6cc; --accent: #0b6e85; }
@media (prefers-color-scheme: dark) { :root { --bg: #0b0e15; --ink: #dfe6f2;
  --muted: #8a94a8; --line: #222a3a; --accent: #58d1eb; } }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 24px 16px 64px; }
h1, h2, h3 { line-height: 1.2; } h2 { margin-top: 48px; border-top: 1px solid var(--line);
  padding-top: 16px; }
.muted { color: var(--muted); } code { color: var(--accent); }
.view { margin: 28px 0; } .shots { display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 380px), 1fr)); }
figure { margin: 0; } img { width: 100%; height: auto; border: 1px solid var(--line);
  border-radius: 6px; } figcaption { font-size: 12px; color: var(--muted); }
ul.check li { margin: 4px 0; }
"""


def render_page(
    shots: Sequence[Shot],
    captions: Mapping[str, Mapping[str, str]],
    *,
    release: str,
    since: str | None,
    changed: Sequence[str] | None,
) -> str:
    """Return the catalog page.

    Args:
        shots: Every snapshot, each with a caption (see :func:`problems`).
        captions: Captions by view.
        release: The release or commit the catalog reflects.
        since: The release the checklist compares with, if any.
        changed: Views changed since ``since``; ``None`` when unknown.

    Returns:
        A self-contained HTML document.
    """
    by_view: dict[str, list[Shot]] = {}
    for shot in shots:
        by_view.setdefault(shot.view, []).append(shot)
    order = {screen: index for index, screen in enumerate(SCREENS)}
    views = sorted(by_view, key=lambda v: (order.get(captions[v]["screen"], len(SCREENS)), v))
    out = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>Console catalog</title><style>{_STYLE}</style></head><body><main>",
        "<h1>Console catalog</h1>",
        f"<p>Release <code>{html.escape(release)}</code>. Every picture is a reviewed test "
        "snapshot with synthetic demo data, so it matches this release's code.</p>",
        "<h2>Validate this deploy</h2>",
    ]
    if changed is None:
        out.append("<p class=muted>No earlier release to compare with: check every view.</p>")
    elif not changed:
        out.append(f"<p>No view changed since <code>{html.escape(since or '')}</code>.</p>")
    else:
        out.append(f"<p>Views changed since <code>{html.escape(since or '')}</code>:</p>")
        out.append("<ul class=check>")
        for view in changed:
            caption = captions.get(view)
            what = (
                f"{caption['screen']} · {caption['state']}: {caption['check']}"
                if caption
                else "removed in this release"
            )
            out.append(f"<li>☐ <code>{html.escape(view)}</code>: {html.escape(what)}</li>")
        out.append("</ul>")
    screen = None
    for view in views:
        caption = captions[view]
        if caption["screen"] != screen:
            screen = caption["screen"]
            out.append(f"<h2>{html.escape(screen)}</h2>")
        out.extend(
            (
                f'<section class=view id="{html.escape(view)}">',
                f"<h3>{html.escape(caption['state'])}</h3>",
                f"<p>{html.escape(caption['means'])}</p>",
                f"<p><b>Check:</b> {html.escape(caption['check'])}</p>",
                f"<p class=muted>Open it: <code>{html.escape(demo_command(caption))}</code> · "
                f"snapshot <code>{html.escape(view)}</code></p>",
                "<div class=shots>",
            )
        )
        shown = sorted(
            by_view[view], key=lambda s: (THEMES.index(s.theme) if s.theme else -1, _size_key(s))
        )
        out.extend(_img(shot) for shot in shown)
        out.append("</div></section>")
    out.append("</main></body></html>")
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Build or check the catalog; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="only check caption coverage")
    parser.add_argument("--release", help="release name to print (default: git describe)")
    parser.add_argument("--since", help="release tag the checklist compares with")
    args = parser.parse_args(argv)

    shots = find_shots()
    captions = load_captions()
    found = problems(shots, captions)
    if found:
        print("console catalog is incomplete:", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1
    if args.check:
        print(f"console catalog: {len(shots)} snapshots, every view captioned")
        return 0

    release = (
        args.release or _git("describe", "--tags", "--match", _RELEASE_TAG, "--always") or "unknown"
    )
    since = args.since or previous_release()
    changed: list[str] | None = None
    if since is not None:
        diff = _git(
            "diff", "--name-only", since, "HEAD", "--", SNAPSHOTS.relative_to(ROOT).as_posix()
        )
        changed = None if diff is None else changed_views(diff.splitlines())
    page = render_page(shots, captions, release=release, since=since, changed=changed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"console catalog: {len(shots)} snapshots -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
