"""DeepSeek chat provider (OpenAI-compatible) — drives the 剧本策划 agent.

实测对比：v4-pro 的梗质量明显好于 v4-flash（剧本是产品，质量优先），默认 pro。
"""

import os

import httpx

DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def build_chat_payload(
    messages: list[dict], *, model: str, json_mode: bool = False
) -> dict:
    payload = {"model": model, "messages": messages, "max_tokens": 4000}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def extract_chat_content(data: dict) -> str:
    choices = data.get("choices") or []
    if choices and choices[0].get("message", {}).get("content"):
        return choices[0]["message"]["content"]
    detail = (data.get("error") or {}).get("message") or str(data)[:300]
    raise RuntimeError(f"DeepSeek returned no content: {detail}")


class DeepSeekChat:
    def __init__(self, model: str | None = None):
        self._api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("对话定制需要 DEEPSEEK_API_KEY 环境变量")
        self._model = model or os.environ.get("MEMEME_DEEPSEEK_MODEL", DEFAULT_MODEL)
        self._base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)

    def __call__(self, messages: list[dict], *, json_mode: bool = False) -> str:
        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=build_chat_payload(messages, model=self._model, json_mode=json_mode),
            timeout=180, trust_env=False,
        )
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")
        return extract_chat_content(data)
