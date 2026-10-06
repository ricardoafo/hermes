"""Draw the "Winged H" console logo to ``src/hermes/entrypoints/assets/winged_h.png``.

A stylised H with a swept wing on each side, filled with a cyan to violet to
magenta gradient on a transparent background. The drawing is original and
deterministic: running it twice writes the same bytes.

Usage: ``uv run python scripts/assets/make_winged_h_logo.py``
"""

from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw

WIDTH: Final = 192
HEIGHT: Final = 96
SUPERSAMPLE: Final = 4
CYAN: Final = (0, 229, 255)
VIOLET: Final = (140, 82, 255)
MAGENTA: Final = (255, 45, 170)
OUTPUT: Final = Path(__file__).resolve().parents[2] / "src/hermes/entrypoints/assets/winged_h.png"

type Point = tuple[float, float]


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _gradient(width: int, height: int) -> Image.Image:
    """Diagonal cyan, violet, magenta gradient, top-left to bottom-right."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None  # noqa: S101
    span = width + height
    for y in range(height):
        for x in range(width):
            t = (x + y) / span
            if t < 0.5:
                pixels[x, y] = _lerp(CYAN, VIOLET, t * 2)
            else:
                pixels[x, y] = _lerp(VIOLET, MAGENTA, (t - 0.5) * 2)
    return image


def _scaled(points: list[Point]) -> list[Point]:
    return [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in points]


def _wing(mirror: bool) -> list[list[Point]]:
    """Three feathers sweeping out and up from the H crossbar."""
    feathers: list[list[Point]] = [
        [(78, 44), (6, 14), (14, 30), (78, 52)],
        [(78, 52), (14, 36), (22, 52), (78, 60)],
        [(78, 60), (24, 58), (34, 72), (78, 68)],
    ]
    if not mirror:
        return feathers
    return [[(WIDTH - x, y) for x, y in feather] for feather in feathers]


def build() -> Image.Image:
    """Return the logo as an RGBA image."""
    mask = Image.new("L", (WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(_scaled([(74, 18), (92, 80)]), fill=255)
    draw.rectangle(_scaled([(100, 18), (118, 80)]), fill=255)
    draw.rectangle(_scaled([(74, 41), (118, 57)]), fill=255)
    for mirror in (False, True):
        for feather in _wing(mirror):
            draw.polygon(_scaled(feather), fill=255)
    mask = mask.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    logo = _gradient(WIDTH, HEIGHT).convert("RGBA")
    logo.putalpha(mask)
    return logo


def main() -> None:
    """Write the PNG next to the Logo widget."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUTPUT, optimize=True)


if __name__ == "__main__":
    main()
