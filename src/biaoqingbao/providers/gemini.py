"""Gemini image provider (BYOK). Needs GEMINI_API_KEY or GOOGLE_API_KEY.

注意：Gemini API 在中国大陆不可直连，需要网络代理（见 docs/DESIGN.md）。
"""

import os
from typing import Any

DEFAULT_MODEL = "gemini-3.1-flash-image"


def extract_image_bytes(response: Any) -> bytes:
    texts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
            if getattr(part, "text", None):
                texts.append(part.text)
    detail = "; ".join(texts) or "empty response"
    raise RuntimeError(f"Gemini returned no image: {detail}")


class GeminiProvider:
    def __init__(self, model: str | None = None):
        from google import genai

        kwargs: dict[str, Any] = {}
        base_url = os.environ.get("BIAOQINGBAO_GEMINI_BASE_URL")
        if base_url:
            from google.genai import types

            kwargs["http_options"] = types.HttpOptions(base_url=base_url)
        # api key read from GEMINI_API_KEY / GOOGLE_API_KEY env
        self._client = genai.Client(**kwargs)
        self._model = model or os.environ.get(
            "BIAOQINGBAO_GEMINI_MODEL", DEFAULT_MODEL
        )

    def generate(self, prompt: str, reference: bytes) -> bytes:
        from google.genai import types

        mime = (
            "image/png"
            if reference[:8] == b"\x89PNG\r\n\x1a\n"
            else "image/jpeg"
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=reference, mime_type=mime),
                prompt,
            ],
            config=types.GenerateContentConfig(
                image_config=types.ImageConfig(aspect_ratio="1:1")
            ),
        )
        return extract_image_bytes(response)
