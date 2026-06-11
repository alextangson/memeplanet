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
