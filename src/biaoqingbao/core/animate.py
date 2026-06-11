"""mp4 → WeChat-spec animated GIF (240×240, ≤500KB).

Quality ladder degrades fps/colors/duration until the size budget holds.
Requires ffmpeg on PATH.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from biaoqingbao.core.postprocess import GIF_MAX_BYTES, STICKER_SIZE

# (fps, palette colors, max seconds)
_LADDER = [(12, 256, 5.0), (10, 128, 4.0), (8, 96, 3.0), (6, 64, 3.0)]

# 程序化动效：每个 effect 是一串 (dx, dy) 帧位移
_EFFECTS = {
    "shake": [(0, 0), (4, 2), (-3, -2), (2, -3)],
    "bounce": [(0, 0), (0, -6), (0, -10), (0, -5)],
}


def _quantize(frame: Image.Image) -> Image.Image:
    alpha = frame.getchannel("A")
    paletted = frame.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT)
    mask = alpha.point(lambda a: 255 if a < 128 else 0)
    paletted.paste(255, mask=mask)
    return paletted


def frames_to_gif(
    frames: list[Image.Image], *, fps: int = 8, max_bytes: int = GIF_MAX_BYTES
) -> bytes:
    paletted = [_quantize(f.convert("RGBA")) for f in frames]
    buf = io.BytesIO()
    paletted[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=int(1000 / fps),
        loop=0,
        transparency=255,
        disposal=2,
    )
    out = buf.getvalue()
    if len(out) > max_bytes:
        raise ValueError(f"GIF exceeds {max_bytes} bytes")
    return out


def procedural_gif(png: bytes, *, effect: str = "shake", fps: int = 8) -> bytes:
    if effect not in _EFFECTS:
        raise ValueError(f"unknown effect: {effect}（可选：{'/'.join(_EFFECTS)}）")
    base = Image.open(io.BytesIO(png)).convert("RGBA")
    frames = []
    for dx, dy in _EFFECTS[effect]:
        canvas = Image.new("RGBA", base.size, (0, 0, 0, 0))
        canvas.paste(base, (dx, dy), base)
        frames.append(canvas)
    return frames_to_gif(frames, fps=fps)


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
