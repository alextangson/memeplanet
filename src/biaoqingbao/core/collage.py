"""九宫格合集晒图卡 — the only attributed artifact in the whole product.

8 stickers + QR in the 9th cell. Singles stay clean; this card is what users
post to Moments/小红书, and the QR is the remix entry point (DESIGN.md premise 4).
"""

import io
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

CELL = 240
PAD = 16
HEADER = 72
FOOTER = 56
COLS = 3
TAGLINE = "换上你的脸，30秒出同款"

_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _qr_cell(url: str) -> Image.Image:
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    return img.resize((CELL, CELL), Image.NEAREST)


def build_collage(stickers: list[bytes], *, pack_name: str, qr_url: str) -> bytes:
    if len(stickers) != 8:
        raise ValueError(f"collage needs exactly 8 stickers, got {len(stickers)}")

    width = COLS * CELL + (COLS + 1) * PAD
    height = HEADER + COLS * CELL + (COLS + 1) * PAD + FOOTER
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    draw.text((PAD, PAD), pack_name, fill=(20, 20, 20, 255), font=_font(40))

    cells = [Image.open(io.BytesIO(s)).convert("RGBA") for s in stickers]
    cells.append(_qr_cell(qr_url))
    for i, cell_img in enumerate(cells):
        col, row = i % COLS, i // COLS
        x = PAD + col * (CELL + PAD)
        y = HEADER + PAD + row * (CELL + PAD)
        cell_img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas.paste(cell_img, (x, y), cell_img)

    draw.text(
        (PAD, height - FOOTER + 8), TAGLINE, fill=(90, 90, 90, 255), font=_font(28)
    )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
