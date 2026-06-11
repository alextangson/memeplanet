"""微信表情开放平台上架素材打包。

平台规格：主图 240×240 PNG/GIF、封面 240×240、聊天面板图标 50×50、
详情页横幅 750×400。提交是人工流程（平台无 API），我们把规格做齐 + 给清单。
"""

import io
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

_GUIDE = """微信表情开放平台上架清单（sticker.weixin.qq.com）

本包内容
- 主图/01.png ~ {n:02d}.png   表情主图（240×240，平台要求 16 或 24 个一套）
- 封面_240.png               表情封面（240×240）
- 图标_50.png                聊天面板图标（50×50）
- 横幅_750x400.png           详情页横幅（750×400）

上架步骤
1. 注册：sticker.weixin.qq.com，个人主体即可（需实名）；
   企业/IP 商用建议企业主体，可关联公众号。
2. 创建表情：选「表情专辑」，按页面要求上传上面的素材。
3. 填写信息：名称、介绍、含义词（每个表情配 1-2 个词，影响搜索曝光）。
4. 注意审核红线：不能含真人肖像争议、商标、二维码、联系方式；
   AI 生成内容如实勾选；动态表情需 GIF（本工具的动图已满足 ≤500KB）。
5. 审核通常 1-7 个工作日，被驳回看原因改了重交，常见原因是
   图片含文字水印、含义词不符、画质模糊。

提示：如果用了 16 张全套生成，主图正好满足一套的最低数量；
本套为 {n} 张{hint}。
"""


def _font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _banner(stickers: list[bytes], pack_name: str) -> bytes:
    canvas = Image.new("RGBA", (750, 400), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    cell = 230
    for i, blob in enumerate(stickers[:3]):
        img = Image.open(io.BytesIO(blob)).convert("RGBA")
        img.thumbnail((cell, cell), Image.LANCZOS)
        canvas.paste(img, (28 + i * (cell + 8), 130), img)
    draw.text((28, 36), pack_name, fill=(30, 30, 30, 255), font=_font(56))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_platform_zip(stickers: list[bytes], *, pack_name: str) -> bytes:
    if not stickers:
        raise ValueError("没有可打包的表情")
    first = Image.open(io.BytesIO(stickers[0])).convert("RGBA")

    cover = io.BytesIO()
    first.resize((240, 240), Image.LANCZOS).save(cover, format="PNG")
    icon = io.BytesIO()
    first.resize((50, 50), Image.LANCZOS).save(icon, format="PNG")

    n = len(stickers)
    hint = "" if n >= 16 else "，再生成全套 16 张可直接达标"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, blob in enumerate(stickers, 1):
            zf.writestr(f"主图/{i:02d}.png", blob)
        zf.writestr("封面_240.png", cover.getvalue())
        zf.writestr("图标_50.png", icon.getvalue())
        zf.writestr("横幅_750x400.png", _banner(stickers, pack_name))
        zf.writestr("上架说明.txt", _GUIDE.format(n=n, hint=hint))
    return buf.getvalue()
