"""Compile a pack's meme script into per-image generation prompts.

Consistency strategy: every prompt carries the identical style block and the
same identity instruction, paired with the same reference photo at call time.
"""

from mememe.core.schema import Meme, Pack

_PROMPT_TEMPLATE = """\
基于参考照片中的人物，生成一张微信表情包贴纸。

【人物一致性】人物必须与参考照片中是同一个人：保持发型、脸型、肤色、眼镜/配饰、
服装等标志性特征，整套表情包中角色形象完全一致。

【画幅】正方形 1:1。只保留人物角色与文案，绝对不要保留或重绘参考照片的背景，
背景必须是纯白色。

【全局风格】
{style}

【本张内容】
- 表情：{expression}
- 动作：{action}
- 镜头：{shot}
- 画面文案（渲染在图内下方）：「{caption}」
"""


def compile_meme(pack: Pack, meme: Meme, caption_override: str | None = None) -> str:
    return _PROMPT_TEMPLATE.format(
        style=pack.style.strip(),
        expression=meme.expression,
        action=meme.action,
        shot=meme.shot,
        caption=caption_override or meme.caption,
    )


def compile_pack(pack: Pack) -> list[str]:
    return [compile_meme(pack, meme) for meme in pack.memes]


_MOTION_TEMPLATE = """\
让画面中的卡通角色动起来：{motion}。
动作幅度适中、自然流畅、适合无缝循环播放。
镜头固定不动，背景保持纯白，角色形象和画面文案保持不变。"""


_KEYFRAME_TEMPLATE = """\
这是一张表情包贴纸。请生成同一张贴纸的下一个动画关键帧：
画风、角色、五官、构图、画面文字、背景全部保持完全不变，
只把动作微调到「{motion}」过程中的另一个瞬间（手臂/身体位置小幅变化即可）。
变化幅度必须小，确保两帧连续播放时形成自然的循环动画。"""


def compile_keyframe(pack: Pack, meme: Meme) -> str:
    motion = meme.motion or meme.action
    return _KEYFRAME_TEMPLATE.format(motion=motion)


def compile_motion(pack: Pack, meme: Meme) -> str:
    motion = meme.motion or f"{meme.expression}，重复做出「{meme.action}」的动作"
    return _MOTION_TEMPLATE.format(motion=motion)
