"""画风预览图重生成：演示脸 × 全部画风，同一个梗保证同脸可比。

烧真实 API 额度——只在画风 prompt 变更后（如构图宪法调整）手动重跑，
别挂 CI、别狂刷。输出直接覆盖 packs/previews/style-{id}.png，并统一规格
（240px 贴纸管线，与用户实际拿到的成品一致）。

用法（在带 key 的终端里跑）:
    .venv/bin/python experiments/style_previews.py             # 全部画风
    .venv/bin/python experiments/style_previews.py felt clay   # 只跑指定画风
    .venv/bin/python experiments/style_previews.py --dry-run   # 只打印 prompt，不调 API

需要 GEMINI_API_KEY（+ MEMEME_GEMINI_BASE_URL 中转，包月主力）；
单张失败自动换即梦兜底（需 ARK_API_KEY），与产品行为一致。
"""

import importlib.util
import sys
from pathlib import Path

from mememe.core.compiler import compile_meme
from mememe.core.postprocess import maybe_remove_background, to_sticker_png
from mememe.core.schema import load_pack
from mememe.core.styles import STYLES

ROOT = Path(__file__).parent.parent
PREVIEW_MEME_INDEX = 3  # shechu liekai：头肩特写，脸最大，画风差异一眼可比


def _provider_chain():
    from mememe.providers.gemini import GeminiProvider

    chain = [("gemini", GeminiProvider())]
    try:
        from mememe.providers.seedream import SeedreamProvider

        chain.append(("seedream", SeedreamProvider()))
    except RuntimeError:
        pass  # 没配 ARK_API_KEY 就不挂兜底
    return chain


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]
    styles = args or list(STYLES)
    unknown = set(styles) - set(STYLES)
    if unknown:
        sys.exit(f"未知画风: {', '.join(sorted(unknown))}")

    pack = load_pack(ROOT / "packs" / "shechu.yaml")
    meme = pack.memes[PREVIEW_MEME_INDEX]
    prompts = {sid: compile_meme(pack, meme, style=sid) for sid in styles}

    if dry_run:
        for sid, prompt in prompts.items():
            print(f"===== {sid} =====\n{prompt}\n")
        return

    reference = (ROOT / "out" / "demo" / "demo-face.png").read_bytes()
    rembg_on = importlib.util.find_spec("rembg") is not None
    chain = _provider_chain()
    failed = []
    for sid, prompt in prompts.items():
        raw = None
        for name, provider in chain:
            try:
                raw = provider.generate(prompt, reference)
                break
            except Exception as e:
                print(f"!! {sid} via {name}: {type(e).__name__}: {e}")
        if raw is None:
            failed.append(sid)
            continue
        png = to_sticker_png(maybe_remove_background(raw, enabled=rembg_on))
        dest = ROOT / "packs" / "previews" / f"style-{sid}.png"
        dest.write_bytes(png)
        print(f"ok {sid} -> {dest.relative_to(ROOT)}")
    if failed:
        sys.exit(f"未完成: {', '.join(failed)}（重跑这几个即可）")


if __name__ == "__main__":
    main()
