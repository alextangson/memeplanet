"""即梦 / 火山方舟 Seedream image provider (BYOK). Needs ARK_API_KEY.

Docs: https://www.volcengine.com/docs/82379/1541523
"""

import base64
import os

import httpx

DEFAULT_MODEL = "doubao-seedream-4-0-250828"  # 1K enough for 240px stickers
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com"

# Seedream 4.5/5.x reject anything under 3,686,400 pixels; older models take 1K.
_HIGH_FLOOR_MARKERS = ("seedream-5", "seedream-4-5")


def _default_size(model: str) -> str:
    if any(m in model for m in _HIGH_FLOOR_MARKERS):
        return "2048x2048"
    return "1024x1024"


def build_payload(
    prompt: str, reference: bytes | None, *, model: str, size: str | None = None
) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size or _default_size(model),
        "response_format": "b64_json",
        "sequential_image_generation": "disabled",
        "stream": False,
        "watermark": False,
    }
    if reference is not None:
        b64 = base64.b64encode(reference).decode()
        payload["image"] = f"data:image/jpeg;base64,{b64}"
    return payload


def extract_seedream_image(data: dict) -> bytes:
    items = data.get("data") or []
    for item in items:
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
    detail = (data.get("error") or {}).get("message") or str(data)[:300]
    raise RuntimeError(f"Seedream returned no image: {detail}")


class SeedreamProvider:
    def __init__(self, model: str | None = None):
        self._api_key = os.environ.get("ARK_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("Seedream 需要 ARK_API_KEY 环境变量")
        self._model = model or os.environ.get("MEMEME_SEEDREAM_MODEL", DEFAULT_MODEL)
        self._size = os.environ.get("MEMEME_SEEDREAM_SIZE") or None
        self._base_url = os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL)

    def generate_text(self, prompt: str) -> bytes:
        """文生图（无参考图）——定制包风格预览用。"""
        return self.generate(prompt, None)

    def generate(self, prompt: str, reference: bytes | None) -> bytes:
        resp = httpx.post(
            f"{self._base_url}/api/v3/images/generations",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=build_payload(prompt, reference, model=self._model, size=self._size),
            timeout=180,
        )
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Seedream HTTP {resp.status_code}: {resp.text[:300]}")
        return extract_seedream_image(data)
