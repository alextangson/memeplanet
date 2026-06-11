import base64

import pytest

from biaoqingbao.providers.seedance import (
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
