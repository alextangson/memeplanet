"""mememe CLI — 一张自拍生成整套微信表情包."""

import importlib.util
from pathlib import Path

import typer

from mememe.core.collage import build_collage
from mememe.core.generate import generate_set, regenerate
from mememe.core.postprocess import (
    maybe_remove_background,
    to_sticker_gif,
    to_sticker_png,
)
from mememe.core.schema import Pack, load_pack
from mememe.providers.base import ImageProvider

app = typer.Typer(help=__doc__, no_args_is_help=True)

DEFAULT_QR_URL = "https://github.com/alextangson/memeplanet"


def _make_provider() -> ImageProvider:
    from mememe.providers.gemini import GeminiProvider

    return GeminiProvider()


def _rembg_available() -> bool:
    return importlib.util.find_spec("rembg") is not None


def _sticker_paths(out: Path, pack: Pack, index: int) -> tuple[Path, Path]:
    stem = f"{index:02d}-{pack.memes[index - 1].id}"
    return out / f"{stem}.png", out / f"{stem}.gif"


def _write_sticker(raw: bytes, out: Path, pack: Pack, index: int, *, remove_bg: bool) -> None:
    processed = maybe_remove_background(raw, enabled=remove_bg)
    png_path, gif_path = _sticker_paths(out, pack, index)
    png_path.write_bytes(to_sticker_png(processed))
    gif_path.write_bytes(to_sticker_gif(processed))


def _rebuild_collage(out: Path, pack: Pack, qr_url: str) -> None:
    stickers = []
    for i in range(1, 9):
        png_path, _ = _sticker_paths(out, pack, i)
        if not png_path.exists():
            typer.echo(f"跳过合集图：缺少 {png_path.name}")
            return
        stickers.append(png_path.read_bytes())
    (out / "collage.png").write_bytes(
        build_collage(stickers, pack_name=pack.name, qr_url=qr_url)
    )
    typer.echo("合集晒图卡 → collage.png")


@app.command()
def validate(pack_path: Path) -> None:
    """校验梗剧本 YAML（CI 和投稿用）。"""
    try:
        pack = load_pack(pack_path)
    except Exception as e:
        typer.echo(f"校验失败：{e}")
        raise typer.Exit(1)
    typer.echo(f"OK：{pack.name}（{len(pack.memes)} 个梗，免费层 {len(pack.free_memes)} 个）")


@app.command()
def generate(
    selfie: Path,
    pack_path: Path = typer.Option(..., "--pack", help="梗剧本 YAML 路径"),
    out: Path = typer.Option(Path("out"), "--out", help="输出目录"),
    full: bool = typer.Option(False, "--full", help="生成全部 16 张（默认免费层 8 张）"),
    remove_bg: bool = typer.Option(
        None, "--remove-bg/--no-remove-bg", help="抠图（默认：装了 rembg 才开）"
    ),
    qr_url: str = typer.Option(DEFAULT_QR_URL, "--qr-url", help="合集图二维码指向"),
) -> None:
    """从一张自拍生成一套表情包 + 合集晒图卡。"""
    pack = load_pack(pack_path)
    reference = selfie.read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    if remove_bg is None:
        remove_bg = _rembg_available()
        if not remove_bg:
            typer.echo("提示：未安装 rembg，跳过抠图（uv sync --extra rembg 可启用）")

    provider = _make_provider()
    count = len(pack.memes) if full else len(pack.free_memes)
    typer.echo(f"开始生成「{pack.name}」{count} 张……")

    state = {"i": 0}

    def on_image(meme, raw: bytes) -> None:
        state["i"] += 1
        _write_sticker(raw, out, pack, state["i"], remove_bg=remove_bg)
        typer.echo(f"  [{state['i']}/{count}] {meme.caption}")

    generate_set(pack, reference, provider, full=full, on_image=on_image)
    _rebuild_collage(out, pack, qr_url)
    typer.echo(f"完成 → {out}/（崩脸的张用 retry 重摇）")


@app.command()
def retry(
    selfie: Path,
    index: int = typer.Argument(..., help="要重摇的序号（1 起，对应文件名前缀）"),
    pack_path: Path = typer.Option(..., "--pack", help="梗剧本 YAML 路径"),
    out: Path = typer.Option(Path("out"), "--out", help="输出目录"),
    remove_bg: bool = typer.Option(None, "--remove-bg/--no-remove-bg"),
    qr_url: str = typer.Option(DEFAULT_QR_URL, "--qr-url"),
) -> None:
    """重摇单张崩脸的表情，并重建合集图。"""
    pack = load_pack(pack_path)
    if remove_bg is None:
        remove_bg = _rembg_available()
    raw = regenerate(pack, selfie.read_bytes(), _make_provider(), index=index)
    _write_sticker(raw, out, pack, index, remove_bg=remove_bg)
    typer.echo(f"已重摇 [{index}] {pack.memes[index - 1].caption}")
    _rebuild_collage(out, pack, qr_url)


@app.command()
def web(
    port: int = typer.Option(8000, "--port", help="监听端口"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（默认仅本机）"),
) -> None:
    """启动本地网页界面（需要 web 依赖：uv sync --extra web）。"""
    try:
        import uvicorn

        from mememe.webapp import create_app
    except ImportError:
        typer.echo("web 依赖未安装：uv sync --extra web")
        raise typer.Exit(1)
    typer.echo(f"表情包工厂 → http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
