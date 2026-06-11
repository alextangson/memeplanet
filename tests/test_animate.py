import io
import shutil
import subprocess

import pytest
from PIL import Image

from biaoqingbao.core.animate import mp4_to_wechat_gif

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="module")
def tiny_mp4(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("vid") / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=480x480:rate=12",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


def test_gif_meets_wechat_spec(tiny_mp4):
    gif = mp4_to_wechat_gif(tiny_mp4)
    img = Image.open(io.BytesIO(gif))
    assert img.format == "GIF"
    assert img.size == (240, 240)
    assert img.n_frames > 1
    assert len(gif) <= 500 * 1024


def _sticker_png() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    for x in range(60, 180):
        for y in range(60, 180):
            img.putpixel((x, y), (200, 80, 30, 255))
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_procedural_shake_gif():
    from biaoqingbao.core.animate import procedural_gif

    gif = procedural_gif(_sticker_png(), effect="shake")
    img = Image.open(io.BytesIO(gif))
    assert img.format == "GIF"
    assert img.size == (240, 240)
    assert img.n_frames >= 4
    assert len(gif) <= 500 * 1024


def test_procedural_unknown_effect_raises():
    from biaoqingbao.core.animate import procedural_gif

    with pytest.raises(ValueError, match="effect"):
        procedural_gif(_sticker_png(), effect="explode")


def test_frames_to_gif_two_frame_loop():
    from biaoqingbao.core.animate import frames_to_gif

    a = Image.open(io.BytesIO(_sticker_png())).convert("RGBA")
    b = Image.new("RGBA", a.size, (0, 0, 0, 0))
    b.paste(a, (20, 10), a)  # genuinely different second frame
    gif = frames_to_gif([a, b], fps=5)
    img = Image.open(io.BytesIO(gif))
    assert img.format == "GIF"
    assert img.n_frames == 2
    assert len(gif) <= 500 * 1024
