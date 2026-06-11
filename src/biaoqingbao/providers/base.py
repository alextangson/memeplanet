from typing import Protocol


class ImageProvider(Protocol):
    def generate(self, prompt: str, reference: bytes) -> bytes:
        """Generate one sticker image. reference = the user's selfie bytes."""
        ...
