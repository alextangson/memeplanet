import io
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

import biaoqingbao.cli as cli

runner = CliRunner()
PACKS_DIR = Path(__file__).parent.parent / "packs"


class FakeProvider:
    def generate(self, prompt: str, reference: bytes) -> bytes:
        img = Image.new("RGBA", (300, 300), (0, 200, 100, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _fake_factory():
    return FakeProvider()


def test_validate_ok():
    result = runner.invoke(cli.app, ["validate", str(PACKS_DIR / "shechu.yaml")])
    assert result.exit_code == 0
    assert "16" in result.output


def test_validate_rejects_broken_pack(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nname: y\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["validate", str(bad)])
    assert result.exit_code != 0


def test_generate_writes_stickers_and_collage(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_provider", _fake_factory)
    selfie = tmp_path / "selfie.jpg"
    Image.new("RGB", (100, 100), (200, 150, 100)).save(selfie)
    out = tmp_path / "out"

    result = runner.invoke(
        cli.app,
        [
            "generate",
            str(selfie),
            "--pack",
            str(PACKS_DIR / "shechu.yaml"),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    pngs = sorted(out.glob("[0-9][0-9]-*.png"))
    gifs = sorted(out.glob("[0-9][0-9]-*.gif"))
    assert len(pngs) == 8
    assert len(gifs) == 8
    assert (out / "collage.png").exists()
    assert pngs[0].name == "01-shoudao.png"


def test_retry_rewrites_single_sticker(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_provider", _fake_factory)
    selfie = tmp_path / "selfie.jpg"
    Image.new("RGB", (100, 100), (200, 150, 100)).save(selfie)
    out = tmp_path / "out"

    runner.invoke(
        cli.app,
        ["generate", str(selfie), "--pack", str(PACKS_DIR / "shechu.yaml"), "--out", str(out)],
    )
    target = out / "04-liekai.png"
    target.write_bytes(b"corrupted")

    result = runner.invoke(
        cli.app,
        [
            "retry",
            str(selfie),
            "4",
            "--pack",
            str(PACKS_DIR / "shechu.yaml"),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert target.read_bytes() != b"corrupted"
    img = Image.open(target)
    assert img.size == (240, 240)


def test_web_command_exists():
    result = runner.invoke(cli.app, ["web", "--help"])
    assert result.exit_code == 0
    assert "端口" in result.output or "port" in result.output.lower()
