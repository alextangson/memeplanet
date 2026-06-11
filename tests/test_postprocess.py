import io

import pytest
from PIL import Image

from mememe.core.postprocess import (
    STICKER_SIZE,
    maybe_remove_background,
    to_sticker_gif,
    to_sticker_png,
)


def _synthetic_image(width: int, height: int) -> bytes:
    img = Image.new("RGBA", (width, height), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_png_is_240_rgba():
    out = to_sticker_png(_synthetic_image(512, 512))
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert img.size == (STICKER_SIZE, STICKER_SIZE)
    assert img.mode == "RGBA"


def test_nonsquare_input_padded_not_distorted():
    out = to_sticker_png(_synthetic_image(200, 100))
    img = Image.open(io.BytesIO(out)).convert("RGBA")
    assert img.size == (STICKER_SIZE, STICKER_SIZE)
    # corners must be transparent padding, center must keep content
    assert img.getpixel((0, 0))[3] == 0
    center = img.getpixel((STICKER_SIZE // 2, STICKER_SIZE // 2))
    assert center[3] == 255 and center[0] > 200


def test_gif_is_240_single_frame_under_500kb():
    out = to_sticker_gif(_synthetic_image(512, 512))
    img = Image.open(io.BytesIO(out))
    assert img.format == "GIF"
    assert img.size == (STICKER_SIZE, STICKER_SIZE)
    assert getattr(img, "n_frames", 1) == 1
    assert len(out) <= 500 * 1024


def test_remove_background_disabled_is_passthrough():
    raw = _synthetic_image(64, 64)
    assert maybe_remove_background(raw, enabled=False) == raw


def test_remove_background_without_rembg_raises_install_hint():
    try:
        import rembg  # noqa: F401

        pytest.skip("rembg installed; hint path not reachable")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="rembg"):
        maybe_remove_background(_synthetic_image(64, 64), enabled=True)
