"""mp4 → WeChat-spec animated GIF (240×240, ≤500KB).

Quality ladder degrades fps/colors/duration until the size budget holds.
Requires ffmpeg on PATH.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from biaoqingbao.core.postprocess import GIF_MAX_BYTES, STICKER_SIZE

# (fps, palette colors, max seconds)
_LADDER = [(12, 256, 5.0), (10, 128, 4.0), (8, 96, 3.0), (6, 64, 3.0)]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True)


def mp4_to_wechat_gif(
    mp4: bytes, *, size: int = STICKER_SIZE, max_bytes: int = GIF_MAX_BYTES
) -> bytes:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("动图转换需要 ffmpeg：brew install ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.mp4"
        src.write_bytes(mp4)
        palette = Path(tmp) / "palette.png"
        out = Path(tmp) / "out.gif"
        scale = f"scale={size}:{size}:flags=lanczos"
        for fps, colors, dur in _LADDER:
            _ffmpeg(
                "-t", str(dur), "-i", str(src),
                "-vf", f"fps={fps},{scale},palettegen=max_colors={colors}",
                str(palette),
            )
            _ffmpeg(
                "-t", str(dur), "-i", str(src), "-i", str(palette),
                "-lavfi", f"fps={fps},{scale}[x];[x][1:v]paletteuse=dither=bayer",
                str(out),
            )
            gif = out.read_bytes()
            if len(gif) <= max_bytes:
                return gif
    raise ValueError(f"GIF 压不进 {max_bytes} 字节，源视频太复杂")
