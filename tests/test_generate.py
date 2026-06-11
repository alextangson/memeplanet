import io
from pathlib import Path

from PIL import Image

from mememe.core.generate import generate_set, regenerate
from mememe.core.schema import load_pack

PACKS_DIR = Path(__file__).parent.parent / "packs"


class FakeProvider:
    def __init__(self):
        self.calls: list[tuple[str, bytes]] = []

    def generate(self, prompt: str, reference: bytes) -> bytes:
        self.calls.append((prompt, reference))
        img = Image.new("RGBA", (300, 300), (0, 128, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


REF = b"fake-selfie-bytes"


def test_generate_set_free_tier_calls_provider_per_free_meme():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    provider = FakeProvider()
    images = generate_set(pack, REF, provider)
    assert len(images) == 8
    assert len(provider.calls) == 8
    # same reference photo on every call — the consistency anchor
    assert all(ref == REF for _, ref in provider.calls)
    # prompts follow pack order
    assert pack.memes[0].caption in provider.calls[0][0]
    assert pack.memes[7].caption in provider.calls[7][0]


def test_generate_set_full_tier():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    provider = FakeProvider()
    images = generate_set(pack, REF, provider, full=True)
    assert len(images) == 16


def test_generate_set_reports_progress():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    seen: list[str] = []
    generate_set(
        pack, REF, FakeProvider(), on_image=lambda meme, _: seen.append(meme.id)
    )
    assert seen == [m.id for m in pack.memes[:8]]


def test_regenerate_targets_single_meme_by_index():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    provider = FakeProvider()
    regenerate(pack, REF, provider, index=4)
    assert len(provider.calls) == 1
    assert pack.memes[3].caption in provider.calls[0][0]  # index is 1-based


def test_regenerate_with_caption_override():
    pack = load_pack(PACKS_DIR / "shechu.yaml")
    provider = FakeProvider()
    regenerate(pack, REF, provider, index=1, caption="老板再见")
    assert "老板再见" in provider.calls[0][0]
    assert pack.memes[0].caption not in provider.calls[0][0]
