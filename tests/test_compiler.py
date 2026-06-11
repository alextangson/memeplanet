from pathlib import Path

from biaoqingbao.core.compiler import compile_pack
from biaoqingbao.core.schema import load_pack

PACKS_DIR = Path(__file__).parent.parent / "packs"


def test_one_prompt_per_meme():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    prompts = compile_pack(pack)
    assert len(prompts) == len(pack.memes)


def test_prompt_contains_meme_fields():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    prompts = compile_pack(pack)
    for meme, prompt in zip(pack.memes, prompts):
        assert meme.caption in prompt
        assert meme.expression in prompt
        assert meme.action in prompt
        assert meme.shot in prompt


def test_style_block_identical_across_all_prompts():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    prompts = compile_pack(pack)
    style = pack.style.strip()
    for prompt in prompts:
        assert style in prompt


def test_prompt_demands_identity_from_reference_photo():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    prompts = compile_pack(pack)
    for prompt in prompts:
        assert "参考照片" in prompt


def test_prompt_demands_square_aspect():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    for prompt in compile_pack(pack):
        assert "1:1" in prompt


def test_caption_override_replaces_text():
    from biaoqingbao.core.compiler import compile_meme

    pack = load_pack(PACKS_DIR / "shechu.yaml")
    prompt = compile_meme(pack, pack.memes[0], caption_override="老板再见")
    assert "老板再见" in prompt
    assert pack.memes[0].caption not in prompt


def test_compile_motion_uses_motion_or_falls_back():
    from biaoqingbao.core.compiler import compile_motion

    pack = load_pack(PACKS_DIR / "shechu.yaml")
    meme = pack.memes[0]
    prompt = compile_motion(pack, meme)
    assert meme.action in prompt          # fallback: action drives the motion
    assert "循环" in prompt
    assert "纯白" in prompt

    meme_with_motion = meme.model_copy(update={"motion": "手臂反复敬礼"})
    prompt2 = compile_motion(pack, meme_with_motion)
    assert "手臂反复敬礼" in prompt2


def test_compile_keyframe_is_minimal_edit_instruction():
    from biaoqingbao.core.compiler import compile_keyframe

    pack = load_pack(PACKS_DIR / "shechu.yaml")
    meme = pack.memes[0]
    instr = compile_keyframe(pack, meme)
    assert "保持" in instr and "不变" in instr   # minimal-change edit contract
    assert meme.action in instr or meme.motion in instr
