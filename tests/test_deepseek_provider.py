import pytest

from mememe.providers.deepseek import build_chat_payload, extract_chat_content


def test_build_chat_payload_plain():
    msgs = [{"role": "user", "content": "你好"}]
    p = build_chat_payload(msgs, model="deepseek-v4-pro")
    assert p["model"] == "deepseek-v4-pro"
    assert p["messages"] == msgs
    assert "response_format" not in p


def test_build_chat_payload_json_mode():
    p = build_chat_payload([], model="m", json_mode=True)
    assert p["response_format"] == {"type": "json_object"}


def test_extract_content():
    data = {"choices": [{"message": {"content": "回复文本"}}]}
    assert extract_chat_content(data) == "回复文本"


def test_extract_error_surfaces_message():
    with pytest.raises(RuntimeError, match="quota"):
        extract_chat_content({"error": {"message": "quota exceeded"}})
