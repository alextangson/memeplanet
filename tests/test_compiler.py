from pathlib import Path

from mememe.core.compiler import compile_pack
from mememe.core.schema import load_pack

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
    from mememe.core.compiler import compile_meme

    pack = load_pack(PACKS_DIR / "shechu.yaml")
    prompt = compile_meme(pack, pack.memes[0], caption_override="老板再见")
    assert "老板再见" in prompt
    assert pack.memes[0].caption not in prompt


def test_compile_motion_uses_motion_or_falls_back():
    from mememe.core.compiler import compile_motion

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
    from mememe.core.compiler import compile_keyframe

    pack = load_pack(PACKS_DIR / "shechu.yaml")
    meme = pack.memes[0]
    instr = compile_keyframe(pack, meme)
    assert "保持" in instr and "不变" in instr   # minimal-change edit contract
    assert meme.action in instr or meme.motion in instr


def test_pet_pack_uses_pet_identity_block():
    from mememe.core.schema import Pack

    base = {
        "id": "t", "name": "测试", "style": "Q版贴纸风格",
        "memes": [{"id": "a", "caption": "好", "expression": "笑",
                   "action": "站", "shot": "半身"}],
    }
    person_prompt = compile_pack(Pack.model_validate(base))[0]
    pet_prompt = compile_pack(Pack.model_validate({**base, "subject": "pet"}))[0]
    assert "发型" in person_prompt and "毛色" not in person_prompt
    assert "毛色" in pet_prompt and "发型" not in pet_prompt
    assert "宠物" in pet_prompt


def test_pet_identity_forbids_species_swap():
    """实测狗子套里画出过猫——物种必须有硬性负面约束。"""
    from mememe.core.schema import Pack

    base = {
        "id": "t", "name": "测试", "style": "贴纸风", "subject": "pet",
        "memes": [{"id": "a", "caption": "汪", "expression": "笑",
                   "action": "摇尾巴", "shot": "全身"}],
    }
    prompt = compile_pack(Pack.model_validate(base))[0]
    assert "物种" in prompt
    assert "严禁" in prompt and "其他动物" in prompt


def test_prompt_forbids_scene_backgrounds_globally():
    """定制包的梗描述常带场景词，模板要兜底：只画道具不画环境。"""
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    for prompt in compile_pack(pack):
        assert "不画环境" in prompt


def test_motion_override_replaces_default():
    from mememe.core.compiler import compile_keyframe, compile_motion

    pack = load_pack(PACKS_DIR / "shechu.yaml")
    meme = pack.memes[0]
    assert "疯狂挥手再见" in compile_motion(pack, meme, motion_override="疯狂挥手再见")
    assert meme.action not in compile_motion(pack, meme, motion_override="疯狂挥手再见")
    assert "疯狂挥手再见" in compile_keyframe(pack, meme, motion_override="疯狂挥手再见")


def test_group_pack_identity_block():
    from mememe.core.schema import Pack

    base = {
        "id": "t", "name": "测试", "style": "贴纸风", "subject": "group",
        "memes": [{"id": "a", "caption": "好", "expression": "笑",
                   "action": "站", "shot": "半身"}],
    }
    prompt = compile_pack(Pack.model_validate(base))[0]
    assert "所有人" in prompt and "各自" in prompt
    assert "人物组合" in prompt
