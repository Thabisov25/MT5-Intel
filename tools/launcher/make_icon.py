"""Draw the MT5 intel app icon and write assets/mt5-intel.ico (multi-size).

A dark navy tile with a small ascending candlestick chart and an amber trend line.
Deliberately simple shapes so it still reads as "a chart" at 16 px in the taskbar.

Run with any Python that has Pillow:  python tools/launcher/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[2] / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

SS = 4  # supersample factor for clean edges
BASE = 256
W = BASE * SS

NAVY = (13, 26, 43, 255)
NAVY_LIP = (30, 52, 78, 255)
GRID = (148, 176, 210, 26)
MINT = (61, 220, 151, 255)
SLATE = (126, 150, 178, 255)
AMBER = (240, 180, 41, 255)


def px(frac: float, span: float, origin: float) -> float:
    """Map a 0..1 fraction inside a span to pixels."""
    return origin + frac * span


def build() -> Image.Image:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Tile
    d.rounded_rectangle([0, 0, W - 1, W - 1], radius=int(W * 0.225), fill=NAVY)
    d.rounded_rectangle(
        [int(W * 0.012), int(W * 0.012), W - 1 - int(W * 0.012), W - 1 - int(W * 0.012)],
        radius=int(W * 0.215),
        outline=NAVY_LIP,
        width=max(1, int(W * 0.008)),
    )

    # Inner plot area
    left, right = px(0.20, W, 0), px(0.80, W, 0)
    top, bottom = px(0.20, W, 0), px(0.80, W, 0)

    # Faint grid lines
    for i in range(1, 4):
        y = top + (bottom - top) * i / 4
        d.line([left, y, right, y], fill=GRID, width=max(1, int(W * 0.004)))

    # Three candles - (centre, open, close, high, low) as 0..1 of the plot, 0 = high price.
    # Few and wide so each body still reads at 16 px; the mint/slate contrast carries the shape.
    candles = [
        (0.16, 0.40, 0.60, 0.30, 0.72),
        (0.50, 0.62, 0.36, 0.72, 0.26),
        (0.84, 0.44, 0.20, 0.54, 0.12),
    ]
    body_w = (right - left) * 0.245
    wick_w = max(1, int(W * 0.014))

    # The trend line runs behind the candles so the bodies stay legible.
    points = [
        (left + (right - left) * cx, top + (bottom - top) * cl)
        for cx, _op, cl, _hi, _lo in candles
    ]
    d.line(points, fill=AMBER, width=max(2, int(W * 0.012)), joint="curve")

    for cx, op, cl, hi, lo in candles:
        x = left + (right - left) * cx
        y_hi = top + (bottom - top) * hi
        y_lo = top + (bottom - top) * lo
        y_op = top + (bottom - top) * op
        y_cl = top + (bottom - top) * cl
        colour = MINT if cl < op else SLATE
        d.line([x, y_hi, x, y_lo], fill=colour, width=wick_w)
        y_top, y_bot = min(y_op, y_cl), max(y_op, y_cl)
        d.rounded_rectangle(
            [x - body_w / 2, y_top, x + body_w / 2, y_bot],
            radius=body_w * 0.24,
            fill=colour,
        )

    return img


def main() -> None:
    img = build()
    sizes = [16, 20, 24, 32, 48, 64, 128, 256]

    ico = ASSETS / "mt5-intel.ico"
    img.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO", sizes=[(s, s) for s in sizes]
    )
    img.resize((256, 256), Image.LANCZOS).save(ASSETS / "mt5-intel.png", format="PNG")

    print(f"Wrote {ico}")
    print(f"Wrote {ASSETS / 'mt5-intel.png'}")


if __name__ == "__main__":
    main()
