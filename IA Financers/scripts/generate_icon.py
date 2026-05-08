"""Gera assets/app.ico com um logo simples do AI Trader Copilot.

Executar uma única vez (ou sempre que quiser regenerar):
    .\venv\Scripts\python.exe scripts\generate_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "app.ico"
PNG_OUT = ROOT / "assets" / "app.png"


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Fundo gradiente simulado em círculo (azul escuro -> ciano)
    for r in range(size // 2, 0, -1):
        t = r / (size / 2)
        col = (
            int(17 + (34 - 17) * (1 - t)),
            int(24 + (211 - 24) * (1 - t)),
            int(39 + (238 - 39) * (1 - t)),
            255,
        )
        bbox = (size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r)
        d.ellipse(bbox, fill=col)

    # Mini "candlestick" branco no centro
    cw = max(2, size // 22)
    cx = size // 2
    base_y = int(size * 0.72)
    top_y = int(size * 0.28)
    # Linha vertical (pavio)
    d.line([(cx, top_y), (cx, base_y)], fill=(255, 255, 255, 255), width=cw)
    # Corpo
    body_w = max(6, size // 6)
    body_top = int(size * 0.40)
    body_bot = int(size * 0.62)
    d.rectangle(
        [cx - body_w // 2, body_top, cx + body_w // 2, body_bot],
        fill=(16, 185, 129, 255),  # verde
        outline=(255, 255, 255, 255),
        width=max(1, size // 64),
    )

    # Setinha de tendência (linha branca diagonal)
    d.line(
        [(int(size * 0.15), int(size * 0.78)),
         (int(size * 0.50), int(size * 0.55)),
         (int(size * 0.85), int(size * 0.30))],
        fill=(255, 255, 255, 220),
        width=max(2, size // 40),
    )
    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [make_icon(s) for s in sizes]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # PNG grande para preview
    images[-1].save(PNG_OUT, format="PNG")
    # ICO multi-resolução
    images[-1].save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"Gerado: {OUT}")
    print(f"Preview PNG: {PNG_OUT}")


if __name__ == "__main__":
    main()
