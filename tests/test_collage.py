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


def test_collage_is_png_with_3x3_grid_geometry():
    out = build_collage(_eight_stickers(), pack_name="社畜的一天", qr_url="https://example.com/r/shechu")
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    # 3 columns wide; height = header + 3 rows + footer, so taller than wide
    assert img.width >= 3 * 240
    assert img.height > img.width


def test_collage_requires_exactly_eight_stickers():
    with pytest.raises(ValueError, match="8"):
        build_collage(_eight_stickers()[:5], pack_name="x", qr_url="https://e.com")


def test_collage_deterministic_for_same_input():
    a = build_collage(_eight_stickers(), pack_name="社畜的一天", qr_url="https://example.com/r/shechu")
    b = build_collage(_eight_stickers(), pack_name="社畜的一天", qr_url="https://example.com/r/shechu")
    assert a == b
