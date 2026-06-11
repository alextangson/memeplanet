"""即梦 / 火山方舟 Seedream image provider (BYOK). Needs ARK_API_KEY.

Docs: https://www.volcengine.com/docs/82379/1541523
"""

import base64
import os

import httpx

DEFAULT_MODEL = "doubao-seedream-5-0-260128"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com"


def build_payload(prompt: str, reference: bytes, *, model: str) -> dict:
    b64 = base64.b64encode(reference).decode()
    return {
        "model": model,
        "prompt": prompt,
        "image": f"data:image/jpeg;base64,{b64}",
        "size": "2048x2048",
        "response_format": "b64_json",
        "sequential_image_generation": "disabled",
        "stream": False,
        "watermark": False,
    }


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
        self._model = model or os.environ.get("BIAOQINGBAO_SEEDREAM_MODEL", DEFAULT_MODEL)
        self._base_url = os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL)

    def generate(self, prompt: str, reference: bytes) -> bytes:
        resp = httpx.post(
            f"{self._base_url}/api/v3/images/generations",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=build_payload(prompt, reference, model=self._model),
            timeout=180,
        )
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Seedream HTTP {resp.status_code}: {resp.text[:300]}")
        return extract_seedream_image(data)
