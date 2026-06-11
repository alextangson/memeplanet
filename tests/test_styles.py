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
    # 注：曾要求画风"自带签名构图"，生产实测构图歪斜被用户判定为败笔，
    # 已反转——构图统一由 compiler 宪法管（见下方 no_composition_overrides）。
    for sid in NEW_STYLE_IDS:
        assert sid in STYLES, sid
        s = STYLES[sid]
        assert s["name"] and s["desc"] and s["block"]


def test_new_style_ids_no_collisions():
    assert not set(NEW_STYLE_IDS) & set(CAPTION_STYLES)


def test_compile_with_new_style_keeps_invariants():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    p = compile_meme(pack, pack.memes[0], style="felt")
    assert STYLES["felt"]["block"] in p
    # 画风覆盖尾注仍强制一致性、居中构图与白底
    assert "主体一致性、居中端正的构图、纯白背景、白色描边" in p


def test_every_style_has_preview_asset():
    for sid in STYLES:
        assert (PACKS_DIR / "previews" / f"style-{sid}.png").exists(), sid


def test_style_blocks_carry_no_composition_overrides():
    # 表情包宪法：构图由 compiler 统一管，画风只准描述材质/线条/配色/光影。
    # 历史教训：felt/pop/lineart/crayon/sticker 曾自带"偏移/倾斜/缩小"构图指令，
    # 海报好看，表情包灾难（微信 240px 小图需要居中满幅端正）。
    banned = ["倾斜", "偏向", "贴边", "一角", "一侧", "构图："]
    for sid, s in STYLES.items():
        for word in banned:
            assert word not in s["block"], f"画风 {sid} 携带构图指令「{word}」"
