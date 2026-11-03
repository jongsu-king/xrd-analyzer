"""Generate the application icon (PNG, Windows .ico, macOS .icns) from code.

Usage: python packaging/make_icons.py   (needs matplotlib and Pillow)
"""

from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch
from PIL import Image

OUT = Path(__file__).resolve().parent / "icons"


def draw(size: int = 1024) -> Path:
    fig = Figure(figsize=(1, 1), dpi=size)
    fig.patch.set_alpha(0.0)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(
        FancyBboxPatch(
            (0.06, 0.06),
            0.88,
            0.88,
            boxstyle="round,pad=0,rounding_size=0.2",
            facecolor="#0b3d91",
            edgecolor="none",
        )
    )
    x = np.linspace(0.14, 0.86, 600)
    y = 0.24 + 0.03 * np.exp(-(((x - 0.2) / 0.25) ** 2))
    for c, h, w in (
        (0.32, 0.50, 0.012),
        (0.52, 0.30, 0.014),
        (0.66, 0.40, 0.013),
        (0.80, 0.16, 0.016),
    ):
        y = y + h / (1 + ((x - c) / w) ** 2)
    ax.plot(x, y, color="#ffffff", lw=2.4, solid_capstyle="round", solid_joinstyle="round")
    ax.plot([0.14, 0.86], [0.2, 0.2], color="#7fb2ff", lw=1.2)
    png = OUT / "xrd-analyzer.png"
    fig.savefig(png, transparent=True)
    return png


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png = draw()
    img = Image.open(png).convert("RGBA")
    img.save(
        OUT / "xrd-analyzer.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    img.save(OUT / "xrd-analyzer.icns")
    img.resize((256, 256), Image.LANCZOS).save(OUT / "xrd-analyzer-256.png")
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
