import base64

import pytest

from mememe.providers.seedream import build_payload, extract_seedream_image


def test_build_payload_embeds_reference_as_data_url():
    payload = build_payload("画一张贴纸", b"JPEGBYTES", model="doubao-seedream-5-0-260128")
    assert payload["model"] == "doubao-seedream-5-0-260128"
    assert payload["prompt"] == "画一张贴纸"
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(payload["image"].split(",", 1)[1]) == b"JPEGBYTES"
    assert payload["response_format"] == "b64_json"
    assert payload["watermark"] is False
    assert payload["size"] == "2048x2048"


def test_size_follows_model_floor():
    # 5.x / 4.5 enforce a 3686400-pixel minimum; older models allow 1K
    assert build_payload("p", b"x", model="doubao-seedream-5-0-260128")["size"] == "2048x2048"
    assert build_payload("p", b"x", model="doubao-seedream-4-5-251128")["size"] == "2048x2048"
    assert build_payload("p", b"x", model="doubao-seedream-4-0-250828")["size"] == "1024x1024"


def test_explicit_size_override_wins():
    payload = build_payload("p", b"x", model="doubao-seedream-4-0-250828", size="2048x2048")
    assert payload["size"] == "2048x2048"


def test_extract_b64_image():
    raw = base64.b64encode(b"PNGDATA").decode()
    assert extract_seedream_image({"data": [{"b64_json": raw}]}) == b"PNGDATA"


def test_extract_empty_raises_with_detail():
    with pytest.raises(RuntimeError, match="quota"):
        extract_seedream_image({"error": {"message": "quota exceeded"}})


def test_build_payload_without_reference_is_t2i():
    payload = build_payload("画一只树懒", None, model="doubao-seedream-4-0-250828")
    assert "image" not in payload
    assert payload["prompt"] == "画一只树懒"
