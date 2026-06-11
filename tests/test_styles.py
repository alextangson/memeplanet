from pathlib import Path

from mememe.core.compiler import compile_meme
from mememe.core.schema import load_pack
from mememe.core.styles import CAPTION_STYLES, STYLES

PACKS_DIR = Path(__file__).parent.parent / "packs"


def test_style_catalog_shape():
    assert "bojack" in STYLES and "anime" in STYLES
    for s in STYLES.values():
        assert s["name"] and s["block"]
    assert "bold" in CAPTION_STYLES
    assert "" not in STYLES  # 默认风格不在目录里，留空即默认


def test_compile_with_style_override_appends_section():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    meme = pack.memes[0]
    base = compile_meme(pack, meme)
    styled = compile_meme(pack, meme, style="bojack")
    assert "【画风指定】" not in base
    assert "【画风指定】" in styled
    assert STYLES["bojack"]["block"] in styled
    assert pack.style.strip() in styled  # 剧本语义（如拟猫化）仍保留


def test_compile_with_caption_style():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    styled = compile_meme(pack, pack.memes[0], caption_style="bold")
    assert "【文字样式】" in styled
    assert CAPTION_STYLES["bold"]["block"] in styled


def test_unknown_style_ignored():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    assert compile_meme(pack, pack.memes[0], style="nope") == compile_meme(
        pack, pack.memes[0]
    )
