import base64

import pytest

from mememe.providers.seedance import (
    build_video_payload,
    extract_task_id,
    extract_video_url,
)


def test_build_video_payload():
    payload = build_video_payload("敬礼动起来", b"PNG", model="doubao-seedance-1-0-pro-250528")
    assert payload["model"] == "doubao-seedance-1-0-pro-250528"
    text = payload["content"][0]["text"]
    assert "敬礼动起来" in text
    assert "--resolution 480p" in text
    assert "--duration 5" in text
    assert "--watermark false" in text
    url = payload["content"][1]["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]) == b"PNG"


def test_extract_task_id():
    assert extract_task_id({"id": "cgt-123"}) == "cgt-123"
    with pytest.raises(RuntimeError, match="denied"):
        extract_task_id({"error": {"message": "denied"}})


def test_extract_video_url():
    assert (
        extract_video_url({"status": "succeeded", "content": {"video_url": "https://v/x.mp4"}})
        == "https://v/x.mp4"
    )
    assert extract_video_url({"status": "running"}) is None
    with pytest.raises(RuntimeError, match="boom"):
        extract_video_url({"status": "failed", "error": {"message": "boom"}})


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def _mock_ark(monkeypatch, deleted: list):
    import mememe.providers.seedance as sd

    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setattr(sd.httpx, "post", lambda *a, **k: _FakeResp({"id": "cgt-42"}))
    monkeypatch.setattr(sd.httpx, "get", lambda *a, **k: _FakeResp({"status": "running"}))
    monkeypatch.setattr(
        sd.httpx, "delete", lambda url, **k: deleted.append(url) or _FakeResp({})
    )
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    return sd


def test_animate_timeout_names_task_and_cancels(monkeypatch):
    # 超时报错必须带 task_id（方舟控制台可查），并尽力取消任务省钱
    deleted: list = []
    sd = _mock_ark(monkeypatch, deleted)
    with pytest.raises(RuntimeError, match="cgt-42"):
        sd.SeedanceVideoProvider().animate("动", b"PNG", timeout=0.05)
    assert any("cgt-42" in u for u in deleted)


def test_animate_timeout_default_from_env(monkeypatch):
    deleted: list = []
    sd = _mock_ark(monkeypatch, deleted)
    monkeypatch.setenv("MEMEME_SEEDANCE_TIMEOUT", "0.05")
    with pytest.raises(RuntimeError, match="cgt-42"):
        sd.SeedanceVideoProvider().animate("动", b"PNG")
