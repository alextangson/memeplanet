"""新画风样张验证：演示脸 × 新画风，每风格 2 张（特写 + 全身各一）。

烧真实 API 额度——只在画风验证时手动运行，别挂 CI、别狂刷。
用法:
    .venv/bin/python experiments/style_samples.py              # 全部 6 个新画风
    .venv/bin/python experiments/style_samples.py felt clay    # 只跑指定画风
需要环境变量 ARK_API_KEY（用户自持，勿写进任何文件）。
"""

import sys
from pathlib import Path

from mememe.core.compiler import compile_meme
from mememe.core.schema import load_pack
from mememe.providers.seedream import SeedreamProvider

ROOT = Path(__file__).parent.parent
NEW_STYLE_IDS = ["felt", "clay", "pop", "lineart", "crayon", "sticker"]
# 固定验证 2 个梗，覆盖两类镜头：liekai=头肩特写、xiaban-chongci=全身
MEME_INDEXES = [3, 7]


def main() -> None:
    styles = sys.argv[1:] or NEW_STYLE_IDS
    pack = load_pack(ROOT / "packs" / "shechu.yaml")
    reference = (ROOT / "out" / "demo" / "demo-face.png").read_bytes()
    provider = SeedreamProvider()
    for sid in styles:
        for idx in MEME_INDEXES:
            meme = pack.memes[idx]
            png = provider.generate(compile_meme(pack, meme, style=sid), reference)
            dest = ROOT / "out" / "style-samples" / sid / f"{meme.id}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(png)
            print(f"ok {sid}/{meme.id} -> {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
