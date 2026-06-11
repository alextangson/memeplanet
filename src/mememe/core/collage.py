"""品牌贴纸海报风合集晒图卡 — the only attributed artifact in the whole product.

8 stickers + a brand CTA tile in a 3×3 scatter on punchy yellow; the site URL
is printed in plain text at the bottom. No QR code: 小红书 shadowbans notes
that contain QR codes, and this card is meant to be posted there directly.
Everything is deterministic: same input, same bytes.
"""

import io
from pathlib import Path

from PIL import Image, ImageDraw

from mememe.core.fonts import cjk_font as _font

W, H = 1080, 1440  # 3:4 朋友圈/小红书原生比例
YELLOW = (255, 210, 61, 255)
ORANGE = (240, 83, 30, 255)
INK = (26, 21, 5, 255)
WHITE = (255, 255, 255, 255)
TAGLINE = "换上你的脸，30秒出同款"

CELL = 330
GRID_X, GRID_Y = 45, 270
STICKER = 290
# 每格固定的旋转/抖动（确定性散落感，不用随机）
ROTATIONS = [-6, 4, -3, 5, -5, 3, -4, 6, -3]
JITTERS = [(-8, 6), (10, -4), (-4, 10), (6, 8), (-10, -6), (8, 4), (-6, -8), (4, -10), (0, 4)]


def _paste_with_shadow(canvas: Image.Image, layer: Image.Image, xy: tuple[int, int]) -> None:
    """弹跳阴影：贴纸轮廓的墨色投影偏移在右下。"""
    alpha = layer.getchannel("A").point(lambda a: a * 30 // 100)
    shadow = Image.new("RGBA", layer.size, INK)
    shadow.putalpha(alpha)
    canvas.paste(shadow, (xy[0] + 8, xy[1] + 10), shadow)
    canvas.paste(layer, xy, layer)


def _display_url(url: str) -> str:
    """链接 → 可读域名：https://meme-planet.com/ → meme-planet.com。"""
    return url.split("://", 1)[-1].rstrip("/")


def _cta_sticker() -> Image.Image:
    """白底圆角贴纸：行星 logo + 「做你的同款」钩子（替代二维码，小红书可发）。"""
    tile = Image.new("RGBA", (STICKER, STICKER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((4, 4, STICKER - 4, STICKER - 4), radius=30, fill=WHITE, outline=INK, width=5)
    tile.alpha_composite(_brand_logo(150), ((STICKER - 150) // 2, 42))
    label = "做你的同款 →"
    font = _font(30)
    tw = draw.textlength(label, font=font)
    draw.text(((STICKER - tw) // 2, 212), label, fill=INK, font=font)
    return tile


def _brand_logo(size: int = 96) -> Image.Image:
    """优先用正式 logo 资产（src/mememe/logo.png），缺失时退回手绘行星。"""
    path = Path(__file__).parents[1] / "logo.png"
    if path.exists():
        img = Image.open(path).convert("RGBA")
        return img.resize((size, size), Image.LANCZOS)
    return _planet_logo(size)


def _planet_logo(size: int = 96) -> Image.Image:
    """行星脸 logo，复刻站内 SVG：墨色环 + 橙色行星 + 笑脸。"""
    s = size * 4  # 4x 超采样抗锯齿
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ring = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((s * 0.03, s * 0.33, s * 0.97, s * 0.67), outline=INK, width=s // 16)
    layer.alpha_composite(ring.rotate(20, resample=Image.BICUBIC))
    d = ImageDraw.Draw(layer)
    d.ellipse((s * 0.17, s * 0.17, s * 0.83, s * 0.83), fill=ORANGE)
    for cx in (0.375, 0.625):
        d.ellipse((s * (cx - 0.055), s * 0.41, s * (cx + 0.055), s * 0.52), fill=INK)
    d.arc((s * 0.36, s * 0.42, s * 0.64, s * 0.66), 20, 160, fill=INK, width=s // 22)
    return layer.resize((size, size), Image.LANCZOS)


def _name_plaque(pack_name: str) -> Image.Image:
    """pack 名做成白底黑描边圆角贴纸牌。"""
    size = 60
    while True:
        font = _font(size)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        tw = probe.textlength(pack_name, font=font)
        if tw <= W - 220 or size <= 28:
            break
        size -= 4
    pad_x, pad_y = 36, 18
    w, h = int(tw) + pad_x * 2 + 10, size + pad_y * 2 + 10
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((5, 5, w - 5, h - 5), radius=h // 3, fill=WHITE, outline=INK, width=5)
    draw.text((pad_x + 5, pad_y), pack_name, fill=INK, font=font)
    return tile


def _accents(draw: ImageDraw.ImageDraw) -> None:
    """四角橙色点缀：圆点 + 四角星，位置写死保持确定性。"""
    for cx, cy, r in ((1000, 200, 12), (66, 250, 9), (1014, 1326, 10), (58, 1210, 13)):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ORANGE)
    for cx, cy, r in ((950, 130, 22), (110, 1310, 18)):
        draw.polygon(
            [(cx, cy - r), (cx + r // 3, cy - r // 3), (cx + r, cy), (cx + r // 3, cy + r // 3),
             (cx, cy + r), (cx - r // 3, cy + r // 3), (cx - r, cy), (cx - r // 3, cy - r // 3)],
            fill=ORANGE,
        )


def _fit(tile: Image.Image, box: int) -> Image.Image:
    """等比缩放到 box 内（小图也放大——海报格子要吃满）。"""
    scale = min(box / tile.width, box / tile.height)
    return tile.resize(
        (round(tile.width * scale), round(tile.height * scale)), Image.LANCZOS
    )


def _frame_sticker(tile: Image.Image) -> Image.Image:
    """矩形底的图加白边圆角贴纸框，统一成贴纸质感。"""
    border, radius = 12, 26
    w, h = tile.width + border * 2, tile.height + border * 2
    framed = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(framed)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=WHITE, outline=INK, width=4)
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, tile.width - 1, tile.height - 1), radius=radius - 8, fill=255
    )
    framed.paste(tile, (border, border), mask)
    return framed


def build_collage(stickers: list[bytes], *, pack_name: str, qr_url: str) -> bytes:
    if len(stickers) != 8:
        raise ValueError(f"collage needs exactly 8 stickers, got {len(stickers)}")

    canvas = Image.new("RGBA", (W, H), YELLOW)
    draw = ImageDraw.Draw(canvas)
    _accents(draw)

    # 顶部品牌区：logo + 站名，下面 pack 名贴纸牌
    canvas.alpha_composite(_brand_logo(96), (52, 36))
    draw.text((164, 58), "表情星球", fill=INK, font=_font(46), stroke_width=1, stroke_fill=INK)
    plaque = _name_plaque(pack_name).rotate(2.5, resample=Image.BICUBIC, expand=True)
    _paste_with_shadow(canvas, plaque, ((W - plaque.width) // 2, 152))

    # 3×3 散落区：8 张贴纸 + 品牌钩子贴纸（第 9 格，无二维码）
    tiles = []
    for s in stickers:
        tile = Image.open(io.BytesIO(s)).convert("RGBA")
        a_min, _ = tile.getchannel("A").getextrema()
        if a_min < 128:  # 已是异形抠图贴纸，免框
            tiles.append(_fit(tile, STICKER))
        else:
            tiles.append(_frame_sticker(_fit(tile, STICKER - 24)))
    tiles.append(_cta_sticker())
    for i, tile in enumerate(tiles):
        rotated = tile.rotate(ROTATIONS[i], resample=Image.BICUBIC, expand=True)
        col, row = i % 3, i // 3
        jx, jy = JITTERS[i]
        x = GRID_X + col * CELL + (CELL - rotated.width) // 2 + jx
        y = GRID_Y + row * CELL + (CELL - rotated.height) // 2 + jy
        _paste_with_shadow(canvas, rotated, (x, y))

    # 底部：标语 + 明文网址（无二维码，小红书可直接发）
    font = _font(44)
    tw = draw.textlength(TAGLINE, font=font)
    draw.text(((W - tw) // 2, 1286), TAGLINE, fill=INK, font=font,
              stroke_width=6, stroke_fill=WHITE)
    site = _display_url(qr_url)
    font = _font(36)
    tw = draw.textlength(site, font=font)
    draw.text(((W - tw) // 2, 1366), site, fill=ORANGE, font=font,
              stroke_width=5, stroke_fill=WHITE)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
