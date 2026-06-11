import io
import zipfile

from PIL import Image

from mememe.core.platform import build_platform_zip


def _stickers(n=8) -> list[bytes]:
    out = []
    for i in range(n):
        img = Image.new("RGBA", (240, 240), (30 * i % 255, 120, 200, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _anim_gif() -> bytes:
    frames = [Image.new("RGB", (240, 240), (i * 60, 80, 90)) for i in range(2)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=120, loop=0)
    return buf.getvalue()


def test_zip_includes_anim_gifs_when_present():
    gifs = [_anim_gif() if i in (0, 6) else None for i in range(8)]
    blob = build_platform_zip(_stickers(), pack_name="社畜的一天", anim_gifs=gifs)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = set(zf.namelist())
    assert "动图/01.gif" in names and "动图/07.gif" in names
    assert "动图/02.gif" not in names
    assert "动图" in zf.read("上架说明.txt").decode("utf-8")


def test_zip_without_anim_gifs_unchanged():
    blob = build_platform_zip(_stickers(), pack_name="社畜的一天")
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert not any(n.startswith("动图/") for n in names)


def test_zip_contains_platform_assets():
    blob = build_platform_zip(_stickers(), pack_name="社畜的一天")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = set(zf.namelist())
    assert "主图/01.png" in names and "主图/08.png" in names
    assert {"封面_240.png", "图标_50.png", "横幅_750x400.png", "上架说明.txt"} <= names

    icon = Image.open(io.BytesIO(zf.read("图标_50.png")))
    assert icon.size == (50, 50)
    banner = Image.open(io.BytesIO(zf.read("横幅_750x400.png")))
    assert banner.size == (750, 400)
    assert "16" in zf.read("上架说明.txt").decode("utf-8")
