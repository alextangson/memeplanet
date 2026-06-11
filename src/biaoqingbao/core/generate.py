"""Serial generation orchestrator. v1: no auto QC, manual per-index reroll."""

from collections.abc import Callable

from biaoqingbao.core.compiler import compile_meme
from biaoqingbao.core.schema import Meme, Pack
from biaoqingbao.providers.base import ImageProvider

ProgressCallback = Callable[[Meme, bytes], None]


def generate_set(
    pack: Pack,
    reference: bytes,
    provider: ImageProvider,
    *,
    full: bool = False,
    on_image: ProgressCallback | None = None,
) -> list[bytes]:
    memes = pack.memes if full else pack.free_memes
    images: list[bytes] = []
    for meme in memes:
        image = provider.generate(compile_meme(pack, meme), reference)
        images.append(image)
        if on_image is not None:
            on_image(meme, image)
    return images


def regenerate(
    pack: Pack,
    reference: bytes,
    provider: ImageProvider,
    *,
    index: int,
    caption: str | None = None,
) -> bytes:
    """Reroll one sticker. index is 1-based, matching CLI output filenames."""
    if not 1 <= index <= len(pack.memes):
        raise ValueError(f"index must be 1..{len(pack.memes)}, got {index}")
    meme = pack.memes[index - 1]
    return provider.generate(compile_meme(pack, meme, caption_override=caption), reference)
