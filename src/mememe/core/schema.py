"""Pack schema — the YAML meme-script format is this project's public API."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

FREE_MEME_COUNT = 8


class Meme(BaseModel):
    id: str = Field(min_length=1)
    caption: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    action: str = Field(min_length=1)
    shot: str = Field(min_length=1)
    motion: str = ""  # 动图的运动描述；留空则由 action 推导


class Pack(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    version: int = 1
    language: str = "zh"
    subject: Literal["person", "pet"] = "person"
    style: str = Field(min_length=1)
    memes: list[Meme] = Field(min_length=1)

    @field_validator("memes")
    @classmethod
    def _unique_meme_ids(cls, memes: list[Meme]) -> list[Meme]:
        seen: set[str] = set()
        for meme in memes:
            if meme.id in seen:
                raise ValueError(f"duplicate meme id: {meme.id}")
            seen.add(meme.id)
        return memes

    @property
    def free_memes(self) -> list[Meme]:
        return self.memes[:FREE_MEME_COUNT]


def load_pack(path: Path | str) -> Pack:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Pack.model_validate(data)
