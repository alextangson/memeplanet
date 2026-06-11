from pathlib import Path

import pytest
from pydantic import ValidationError

from mememe.core.schema import Pack, load_pack

PACKS_DIR = Path(__file__).parent.parent / "packs"


def test_load_shechu_pack():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    assert pack.id == "shechu"
    assert pack.name == "社畜的一天"
    assert len(pack.memes) == 16
    assert pack.style.strip()


def test_every_meme_has_required_fields():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    for meme in pack.memes:
        assert meme.id.strip()
        assert meme.caption.strip()
        assert meme.expression.strip()
        assert meme.action.strip()
        assert meme.shot.strip()


def test_free_memes_is_first_eight():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    assert len(pack.free_memes) == 8
    assert pack.free_memes == pack.memes[:8]
    assert pack.free_memes[0].caption == "收到"


def _minimal_pack_dict():
    return {
        "id": "t",
        "name": "测试",
        "style": "风格",
        "memes": [
            {
                "id": "a",
                "caption": "好",
                "expression": "笑",
                "action": "站",
                "shot": "半身",
            }
        ],
    }


def test_duplicate_meme_ids_rejected():
    data = _minimal_pack_dict()
    data["memes"].append(dict(data["memes"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        Pack.model_validate(data)


def test_missing_caption_rejected():
    data = _minimal_pack_dict()
    del data["memes"][0]["caption"]
    with pytest.raises(ValidationError):
        Pack.model_validate(data)


def test_empty_memes_rejected():
    data = _minimal_pack_dict()
    data["memes"] = []
    with pytest.raises(ValidationError):
        Pack.model_validate(data)


def test_meme_motion_field_optional_with_default():
    data = _minimal_pack_dict()
    pack = Pack.model_validate(data)
    assert pack.memes[0].motion == ""
    data["memes"][0]["motion"] = "敬礼的手上下挥动"
    pack = Pack.model_validate(data)
    assert pack.memes[0].motion == "敬礼的手上下挥动"
