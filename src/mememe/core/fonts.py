"""中文字体定位 — 服务端 PIL 画字（晒图卡 / 上架横幅）的唯一字体来源。

教训：生产 Linux 默认无 CJK 字体，PIL 会静默回退到无中文字形的位图字体，
晒图卡上的中文渲染成豆腐块且不报错。这里用显式候选 + 目录 glob 兜底，
尽量找到机器上任何一款中文字体；真找不到时打日志（而不是无声降级）。

部署依赖：服务器需装中文字体，如 Ubuntu `apt install fonts-noto-cjk`。
"""

import logging
from pathlib import Path

from PIL import ImageFont

_log = logging.getLogger("mememe.fonts")

# 显式优先级：macOS 开发机 → Linux 常见安装路径
_EXPLICIT = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
# glob 兜底：包布局变了也能找到（含 24.04 的可变字体 *-VF.otf.ttc）
_GLOB_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")]
_GLOB_PATTERNS = ["**/NotoSansCJK*.ttc", "**/NotoSansCJK*.otf", "**/*CJK*.ttc",
                  "**/wqy*.ttc", "**/SourceHanSans*.*", "**/DroidSansFallback*.ttf"]

_cached_path: str | None = None


def _find_font_path() -> str | None:
    global _cached_path
    if _cached_path is not None:
        return _cached_path or None
    for p in _EXPLICIT:
        if Path(p).exists():
            _cached_path = p
            return p
    for d in _GLOB_DIRS:
        base = Path(d)
        if not base.is_dir():
            continue
        for pat in _GLOB_PATTERNS:
            hit = next(base.glob(pat), None)
            if hit:
                _cached_path = str(hit)
                return _cached_path
    _cached_path = ""  # 缓存“找不到”，避免每次画字都重新扫盘
    _log.warning("未找到中文字体，晒图卡/横幅中文将渲染异常；请安装 fonts-noto-cjk")
    return None


def cjk_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    path = _find_font_path()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()
