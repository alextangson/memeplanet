"""对话式剧本策划 agent：聊天收集需求 → 产出经 schema 校验的定制剧本。

agent 只负责"梗的内容"；视觉风格基底由系统注入，保证定制包和官方包
走同一条一致性管线。这是个人定制和 B 端 IP 定制共用的地基。
"""

import json
from collections.abc import Callable

from pydantic import ValidationError

from mememe.core.schema import Pack

SYSTEM_PROMPT = """你是「表情星球」的金牌表情包策划，帮用户定制专属表情包剧本。
对话目标，搞清楚四件事：
1. 表情包发给谁看（自用斗图/情侣/家人/同事群/品牌IP的粉丝群）
2. 主角是谁（用户本人/宠物/吉祥物/品牌人设——之后用户会上传主角照片）
3. 想要的梗方向和语气（自嘲/阴阳/可爱/职业梗……）
4. 有没有必须出现的口头禅、口号或行业黑话
规则：每次只问 1-2 个问题，口语化像朋友聊天，别列清单。
2-4 轮后信息够了就说：信息够啦，点【✨生成剧本】出方案。
不要在聊天里输出完整剧本，剧本由系统按钮生成。"""

DRAFT_INSTRUCTION = """基于以上全部对话，输出这套表情包剧本的 JSON（只输出 JSON，不要任何其他文字）：
{"id":"英文小写连字符id","name":"剧本名(2-6字)","description":"一句话描述",
"subject":"person或pet或group(多人合照)","subject_desc":"主角外观一句话(如：一只穿围裙的树懒玩偶/短发戴眼镜的程序员)",
"vibe":"一句话画风氛围(可选的元素/配色/情绪点缀)",
"memes":[{"id":"英文小写id","caption":"图内文案2-6字","expression":"具体可画的表情描述",
"action":"戏剧化的动作描述","shot":"头肩特写或半身或全身"}]}
要求：memes 必须正好 16 个；前 8 个放用户最高频最想发的；
文案口语化、贴合对话里的真实语境和口头禅；表情和动作要具体、夸张、有画面感。"""

_STYLE_BASE = """Q版三头身（chibi）贴纸插画风格，粗白色描边，背景必须纯白。
主体特征必须与参考照片一致并贯穿全套，整套形象完全一致。
表情夸张、动作戏剧化，微信表情包审美。
文案以大号中文手写体渲染在画面下方居中，字色深、带白边，清晰可读。"""

ChatFn = Callable[..., str]


def _build_style(vibe: str) -> str:
    style = _STYLE_BASE
    if vibe.strip():
        style += f"\n本套氛围：{vibe.strip()}"
    return style


class Scriptwriter:
    def __init__(self, chat: ChatFn):
        self._chat = chat

    def reply(self, history: list[dict]) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
        return self._chat(messages, json_mode=False)

    def draft(self, history: list[dict]) -> Pack:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": DRAFT_INSTRUCTION},
        ]
        last_error: Exception | None = None
        for _ in range(2):
            raw = self._chat(messages, json_mode=True)
            try:
                data = json.loads(raw)
                data["style"] = _build_style(data.pop("vibe", ""))
                data.setdefault("language", "zh")
                return Pack.model_validate(data)
            except (ValueError, ValidationError) as e:
                last_error = e
                messages = messages + [
                    {"role": "assistant", "content": raw[:2000]},
                    {
                        "role": "user",
                        "content": f"上面的 JSON 校验失败：{str(e)[:300]}。"
                        "请修正后重新只输出 JSON。",
                    },
                ]
        raise ValueError(f"剧本生成失败，请再聊两句补充信息后重试：{last_error}")
