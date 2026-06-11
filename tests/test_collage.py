import io

import pytest
from PIL import Image

from mememe.core.collage import build_collage


def _sticker(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGBA", (240, 240), (*color, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _eight_stickers() -> list[bytes]:
    return [_sticker((30 * i, 100, 200 - 20 * i)) for i in range(8)]


def test_collage_is_branded_poster():
    out = build_collage(_eight_stickers(), pack_name="社畜的一天", qr_url="https://example.com/r/shechu")
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert (img.width, img.height) == (1080, 1440)  # 3:4 朋友圈/小红书原生比例
    # 躁动黄打底（品牌色 #FFD23D），不是模型默认的白/米白
    assert img.convert("RGB").getpixel((4, 4)) == (255, 210, 61)


def test_collage_handles_long_pack_name():
    out = build_collage(_eight_stickers(), pack_name="一个特别特别长的定制剧本名字", qr_url="https://e.com")
    img = Image.open(io.BytesIO(out))
    assert (img.width, img.height) == (1080, 1440)


def test_collage_requires_exactly_eight_stickers():
    with pytest.raises(ValueError, match="8"):
        build_collage(_eight_stickers()[:5], pack_name="x", qr_url="https://e.com")


def test_collage_deterministic_for_same_input():
    a = build_collage(_eight_stickers(), pack_name="社畜的一天", qr_url="https://example.com/r/shechu")
    b = build_collage(_eight_stickers(), pack_name="社畜的一天", qr_url="https://example.com/r/shechu")
    assert a == b
