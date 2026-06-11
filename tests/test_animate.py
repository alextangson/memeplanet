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
