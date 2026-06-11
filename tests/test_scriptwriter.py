import json

import pytest

from mememe.core.scriptwriter import DRAFT_INSTRUCTION, Scriptwriter


def _valid_draft() -> dict:
    return {
        "id": "shangxian",
        "name": "上线日",
        "description": "程序员上线日自嘲",
        "subject": "person",
        "vibe": "键盘、显示器元素点缀，疲惫又亢奋",
        "memes": [
            {"id": f"m{i}", "caption": f"梗{i}", "expression": "表情", "action": "动作", "shot": "半身"}
            for i in range(16)
        ],
    }


class FakeChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, *, json_mode=False):
        self.calls.append((messages, json_mode))
        return self.responses.pop(0)


def test_reply_appends_system_prompt():
    chat = FakeChat(["想给谁做表情包呀？"])
    sw = Scriptwriter(chat)
    out = sw.reply([{"role": "user", "content": "我想定制"}])
    assert out == "想给谁做表情包呀？"
    messages, json_mode = chat.calls[0]
    assert messages[0]["role"] == "system"
    assert json_mode is False


def test_draft_returns_validated_pack_with_injected_style():
    chat = FakeChat([json.dumps(_valid_draft(), ensure_ascii=False)])
    sw = Scriptwriter(chat)
    pack = sw.draft([{"role": "user", "content": "程序员上线日"}])
    assert pack.id == "shangxian"
    assert len(pack.memes) == 16
    assert "纯白" in pack.style          # 标准风格基底被注入
    assert "键盘" in pack.style          # vibe 并入风格块
    messages, json_mode = chat.calls[0]
    assert json_mode is True
    assert DRAFT_INSTRUCTION in messages[-1]["content"]


def test_draft_retries_once_on_invalid_then_raises():
    bad = json.dumps({"id": "x", "name": "缺梗"})
    chat = FakeChat([bad, bad])
    sw = Scriptwriter(chat)
    with pytest.raises(ValueError, match="剧本生成失败"):
        sw.draft([{"role": "user", "content": "随便"}])
    assert len(chat.calls) == 2


def test_draft_preserves_subject_desc():
    draft = _valid_draft()
    draft["subject_desc"] = "一只穿围裙的树懒玩偶"
    chat = FakeChat([json.dumps(draft, ensure_ascii=False)])
    pack = Scriptwriter(chat).draft([{"role": "user", "content": "树懒"}])
    assert pack.subject_desc == "一只穿围裙的树懒玩偶"
    assert "subject_desc" in DRAFT_INSTRUCTION
