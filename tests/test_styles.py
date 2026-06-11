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


def test_default_caption_rendering_varies_by_meme_index():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    p0 = compile_meme(pack, pack.memes[0])
    p1 = compile_meme(pack, pack.memes[1])
    assert "【文字样式】" in p0 and "【文字样式】" in p1
    assert p0.split("【文字样式】")[1] != p1.split("【文字样式】")[1]
    # 轮换有周期：第 0 与第 4 个相同
    p4 = compile_meme(pack, pack.memes[4])
    assert p0.split("【文字样式】")[1] == p4.split("【文字样式】")[1]


def test_explicit_caption_style_is_uniform():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    p0 = compile_meme(pack, pack.memes[0], caption_style="bold")
    p1 = compile_meme(pack, pack.memes[1], caption_style="bold")
    assert p0.split("【文字样式】")[1] == p1.split("【文字样式】")[1]
    assert CAPTION_STYLES["bold"]["block"] in p0


NEW_STYLE_IDS = ["felt", "clay", "pop", "lineart", "crayon", "sticker"]


def test_new_styles_registered():
    for sid in NEW_STYLE_IDS:
        assert sid in STYLES, sid
        s = STYLES[sid]
        assert s["name"] and s["desc"] and s["block"]
        assert "构图" in s["block"], f"{sid} 必须自带签名构图"


def test_new_style_ids_no_collisions():
    assert not set(NEW_STYLE_IDS) & set(CAPTION_STYLES)


def test_compile_with_new_style_keeps_invariants():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    p = compile_meme(pack, pack.memes[0], style="felt")
    assert STYLES["felt"]["block"] in p
    # 画风覆盖尾注仍强制一致性与白底
    assert "主体一致性、纯白背景、白色描边" in p
