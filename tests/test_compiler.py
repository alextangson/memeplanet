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
