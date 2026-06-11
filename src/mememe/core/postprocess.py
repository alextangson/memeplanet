"""WeChat sticker spec post-processing.

WeChat custom stickers: 240×240. Chat re-encodes PNGs and drops transparency,
so we emit both transparent PNG and single-frame GIF and field-test which
survives import (DESIGN.md Stage 1).
"""

import io

from PIL import Image

try:  # iPhone 拍照默认 HEIC，注册后 PIL 才能解
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

STICKER_SIZE = 240
GIF_MAX_BYTES = 500 * 1024
SELFIE_MAX_EDGE = 1536  # 上传图归一化上限，省内存也够供应商用


def _open_rgba(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGBA")


def _fit_square(img: Image.Image) -> Image.Image:
    img.thumbnail((STICKER_SIZE, STICKER_SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (STICKER_SIZE, STICKER_SIZE), (0, 0, 0, 0))
    offset = ((STICKER_SIZE - img.width) // 2, (STICKER_SIZE - img.height) // 2)
    canvas.paste(img, offset, img)
    return canvas


def to_sticker_png(image_bytes: bytes) -> bytes:
    canvas = _fit_square(_open_rgba(image_bytes))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def to_sticker_gif(image_bytes: bytes) -> bytes:
    canvas = _fit_square(_open_rgba(image_bytes))
    alpha = canvas.getchannel("A")
    # GIF has 1-bit transparency: quantize to 255 colors, reserve index 255
    paletted = canvas.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT)
    mask = alpha.point(lambda a: 255 if a < 128 else 0)
    paletted.paste(255, mask=mask)
    buf = io.BytesIO()
    paletted.save(buf, format="GIF", transparency=255, optimize=True)
    out = buf.getvalue()
    if len(out) > GIF_MAX_BYTES:
        raise ValueError(f"GIF exceeds WeChat limit: {len(out)} > {GIF_MAX_BYTES}")
    return out


def normalize_selfie(image_bytes: bytes) -> bytes:
    """把上传照片统一成下行安全的 JPEG：解 HEIC、削尺寸、剥 EXIF。

    非图片（伪装的文本/脚本）在这里被 PIL 拒掉，挡在生成管线之外。
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        raise ValueError("无法识别为图片") from e
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > SELFIE_MAX_EDGE:
        img.thumbnail((SELFIE_MAX_EDGE, SELFIE_MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def maybe_remove_background(image_bytes: bytes, *, enabled: bool) -> bytes:
    if not enabled:
        return image_bytes
    try:
        from rembg import remove
    except ImportError as e:
        raise RuntimeError(
            "背景移除需要可选依赖 rembg：uv sync --extra rembg"
        ) from e
    return remove(image_bytes)
