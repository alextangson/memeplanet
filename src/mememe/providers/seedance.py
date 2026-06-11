"""Seedance (火山方舟) image-to-video provider for animated stickers.

Async task API: create → poll → download mp4. Input image must be ≥300px,
so callers feed the RAW generation output, not the 240px sticker.
"""

import base64
import os
import time

import httpx

DEFAULT_MODEL = "doubao-seedance-1-0-pro-250528"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com"
_PARAMS = "--resolution 480p --duration 5 --watermark false"


def build_video_payload(prompt: str, image: bytes, *, model: str) -> dict:
    b64 = base64.b64encode(image).decode()
    return {
        "model": model,
        "content": [
            {"type": "text", "text": f"{prompt} {_PARAMS}"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            },
        ],
    }


def extract_task_id(data: dict) -> str:
    if data.get("id"):
        return data["id"]
    detail = (data.get("error") or {}).get("message") or str(data)[:300]
    raise RuntimeError(f"Seedance task not created: {detail}")


def extract_video_url(data: dict) -> str | None:
    status = data.get("status")
    if status == "succeeded":
        return (data.get("content") or {}).get("video_url")
    if status == "failed":
        detail = (data.get("error") or {}).get("message") or str(data)[:300]
        raise RuntimeError(f"Seedance task failed: {detail}")
    return None


class SeedanceVideoProvider:
    def __init__(self, model: str | None = None):
        self._api_key = os.environ.get("ARK_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("Seedance 需要 ARK_API_KEY 环境变量")
        self._model = model or os.environ.get("MEMEME_SEEDANCE_MODEL", DEFAULT_MODEL)
        self._base_url = os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL)

    def animate(self, prompt: str, image: bytes, *, timeout: float = 300) -> bytes:
        """Returns mp4 bytes."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = httpx.post(
            f"{self._base_url}/api/v3/contents/generations/tasks",
            headers=headers,
            json=build_video_payload(prompt, image, model=self._model),
            timeout=60,
        )
        task_id = extract_task_id(resp.json())

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            status = httpx.get(
                f"{self._base_url}/api/v3/contents/generations/tasks/{task_id}",
                headers=headers,
                timeout=30,
            ).json()
            url = extract_video_url(status)
            if url:
                video = httpx.get(url, timeout=120, follow_redirects=True)
                video.raise_for_status()
                return video.content
        raise RuntimeError(f"Seedance task timed out after {timeout}s")
