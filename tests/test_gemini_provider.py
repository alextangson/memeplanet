from types import SimpleNamespace

import pytest

from biaoqingbao.providers.gemini import extract_image_bytes


def _response(parts: list) -> SimpleNamespace:
    content = SimpleNamespace(parts=parts)
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def test_extracts_inline_image_bytes():
    parts = [
        SimpleNamespace(text="here you go", inline_data=None),
        SimpleNamespace(
            text=None,
            inline_data=SimpleNamespace(data=b"PNGDATA", mime_type="image/png"),
        ),
    ]
    assert extract_image_bytes(_response(parts)) == b"PNGDATA"


def test_no_image_raises_with_model_text():
    parts = [SimpleNamespace(text="safety refusal blah", inline_data=None)]
    with pytest.raises(RuntimeError, match="safety refusal blah"):
        extract_image_bytes(_response(parts))


def test_empty_candidates_raises():
    with pytest.raises(RuntimeError):
        extract_image_bytes(SimpleNamespace(candidates=[]))
