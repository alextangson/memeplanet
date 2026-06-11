from types import SimpleNamespace

import pytest

from mememe.providers.gemini import extract_image_bytes


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


def test_provider_uses_custom_base_url_from_env(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    monkeypatch.setenv("MEMEME_GEMINI_BASE_URL", "https://relay.example/gemini")
    from mememe.providers.gemini import GeminiProvider

    GeminiProvider()
    assert captured["http_options"].base_url == "https://relay.example/gemini"


def test_provider_default_has_no_base_url_override(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    monkeypatch.delenv("MEMEME_GEMINI_BASE_URL", raising=False)
    from mememe.providers.gemini import GeminiProvider

    GeminiProvider()
    assert "http_options" not in captured


def test_generate_passes_square_image_config(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace

            part = SimpleNamespace(
                text=None,
                inline_data=SimpleNamespace(data=b"IMG", mime_type="image/png"),
            )
            content = SimpleNamespace(parts=[part])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    from mememe.providers.gemini import GeminiProvider

    out = GeminiProvider().generate("prompt", b"ref")
    assert out == b"IMG"
    assert captured["config"].image_config.aspect_ratio == "1:1"


def test_generate_sniffs_reference_mime(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace

            part = SimpleNamespace(
                text=None,
                inline_data=SimpleNamespace(data=b"IMG", mime_type="image/png"),
            )
            content = SimpleNamespace(parts=[part])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    from mememe.providers.gemini import GeminiProvider

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest"
    GeminiProvider().generate("p", png_bytes)
    assert captured["contents"][0].inline_data.mime_type == "image/png"

    jpeg_bytes = b"\xff\xd8\xff" + b"rest"
    GeminiProvider().generate("p", jpeg_bytes)
    assert captured["contents"][0].inline_data.mime_type == "image/jpeg"
