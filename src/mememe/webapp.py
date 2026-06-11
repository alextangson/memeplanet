"""Local web UI — FastAPI wrapping core. The Stage 2b service grows from here.

Privacy: the selfie lives in process memory only (retry needs it); it is never
written to disk and vanishes when the server stops.
"""

import importlib.util
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

import io

from PIL import Image

from mememe.core.animate import frames_to_gif, mp4_to_wechat_gif, procedural_gif
from mememe.core.collage import build_collage
from mememe.core.compiler import compile_keyframe, compile_meme, compile_motion
from mememe.core.postprocess import (
    maybe_remove_background,
    to_sticker_gif,
    to_sticker_png,
)
from mememe.core.schema import Pack, load_pack
from mememe.providers.base import ImageProvider

OUTPUT_ROOT = Path("out/web")
PACKS_DIR = Path(os.environ.get("MEMEME_PACKS_DIR", "packs"))
DEFAULT_QR_URL = "https://github.com/REPLACE-ME/mememe"


def _make_provider(name: str = "") -> ImageProvider:
    name = name or os.environ.get("MEMEME_PROVIDER", "gemini")
    if name == "seedream":
        from mememe.providers.seedream import SeedreamProvider

        return SeedreamProvider()
    from mememe.providers.gemini import GeminiProvider

    return GeminiProvider()


def _fallback_image_provider(name: str) -> ImageProvider | None:
    """拼帧编辑对上游错误敏感（中转 500 等）；失败时换另一家试一次。"""
    other = "seedream" if (name or "gemini") == "gemini" else "gemini"
    try:
        return _make_provider(other)
    except Exception:
        return None


def _make_video_provider():
    from mememe.providers.seedance import SeedanceVideoProvider

    return SeedanceVideoProvider()


def _rembg_available() -> bool:
    return importlib.util.find_spec("rembg") is not None


@dataclass
class Job:
    id: str
    pack: Pack | None
    selfie: bytes
    out_dir: Path
    full: bool
    pack_id: str = ""
    pack_name: str = ""
    created_at: float = 0.0
    provider_name: str = ""
    status: str = "running"
    error: str = ""
    images: list[dict] = field(default_factory=list)
    collage_url: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def memes(self):
        if self.pack is None:
            return []
        return self.pack.memes if self.full else self.pack.free_memes


def _sticker_stem(job: Job, index: int) -> str:
    return f"{index:02d}-{job.images[index - 1]['id']}"


def _save_meta(job: Job) -> None:
    """Persist job metadata (never the selfie) so history survives restarts."""
    with job.lock:
        meta = {
            "job_id": job.id,
            "pack_id": job.pack_id,
            "pack_name": job.pack_name,
            "full": job.full,
            "provider_name": job.provider_name,
            "status": job.status,
            "error": job.error,
            "created_at": job.created_at,
            "images": [dict(i) for i in job.images],
            "collage_url": job.collage_url,
        }
    (job.out_dir / "job.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _load_jobs() -> dict[str, Job]:
    jobs: dict[str, Job] = {}
    if not OUTPUT_ROOT.exists():
        return jobs
    for meta_path in OUTPUT_ROOT.glob("*/job.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        pack = None
        pack_file = PACKS_DIR / f"{meta.get('pack_id', '')}.yaml"
        if pack_file.exists():
            try:
                pack = load_pack(pack_file)
            except Exception:
                pack = None
        status = meta.get("status", "done")
        if status == "running":  # server died mid-job
            status = "error"
        jobs[meta["job_id"]] = Job(
            id=meta["job_id"],
            pack=pack,
            selfie=b"",
            out_dir=meta_path.parent,
            full=meta.get("full", False),
            pack_id=meta.get("pack_id", ""),
            pack_name=meta.get("pack_name", ""),
            created_at=meta.get("created_at", 0.0),
            provider_name=meta.get("provider_name", ""),
            status=status,
            error=meta.get("error", ""),
            images=meta.get("images", []),
            collage_url=meta.get("collage_url", ""),
        )
    return jobs


def _write_one(job: Job, index: int, raw: bytes) -> None:
    processed = maybe_remove_background(raw, enabled=_rembg_available())
    stem = _sticker_stem(job, index)
    (job.out_dir / f"raw-{stem}.png").write_bytes(raw)  # animation needs ≥300px source
    (job.out_dir / f"{stem}.png").write_bytes(to_sticker_png(processed))
    (job.out_dir / f"{stem}.gif").write_bytes(to_sticker_gif(processed))


def _rebuild_collage(job: Job) -> None:
    stickers = []
    for i in range(1, 9):
        path = job.out_dir / f"{_sticker_stem(job, i)}.png"
        if not path.exists():
            return
        stickers.append(path.read_bytes())
    (job.out_dir / "collage.png").write_bytes(
        build_collage(stickers, pack_name=job.pack.name, qr_url=DEFAULT_QR_URL)
    )
    job.collage_url = f"/files/{job.id}/collage.png"


def _run_generation(job: Job, provider: ImageProvider) -> None:
    try:
        for pos, meme in enumerate(job.memes):
            index = pos + 1
            with job.lock:
                job.images[pos]["status"] = "running"
            raw = provider.generate(compile_meme(job.pack, meme), job.selfie)
            _write_one(job, index, raw)
            with job.lock:
                job.images[pos]["status"] = "done"
                job.images[pos]["url"] = f"/files/{job.id}/{_sticker_stem(job, index)}.png"
                job.images[pos]["gif_url"] = f"/files/{job.id}/{_sticker_stem(job, index)}.gif"
        _rebuild_collage(job)
        job.status = "done"
    except Exception as e:  # surface, don't swallow — UI shows it
        job.status = "error"
        job.error = str(e)
    _save_meta(job)


def _make_anim_gif(job: Job, index: int, mode: str, provider) -> bytes:
    pos = index - 1
    stem = _sticker_stem(job, index)
    if mode in ("shake", "bounce"):
        png = (job.out_dir / f"{stem}.png").read_bytes()
        return procedural_gif(png, effect=mode)
    if mode == "frames":
        raw = (job.out_dir / f"raw-{stem}.png").read_bytes()
        prompt = compile_keyframe(job.pack, job.pack.memes[pos])
        try:
            alt = provider.generate(prompt, raw)
        except Exception:
            fallback = _fallback_image_provider(job.provider_name)
            if fallback is None:
                raise
            alt = fallback.generate(prompt, raw)
        frames = [
            Image.open(io.BytesIO((job.out_dir / f"{stem}.png").read_bytes())),
            Image.open(
                io.BytesIO(
                    to_sticker_png(
                        maybe_remove_background(alt, enabled=_rembg_available())
                    )
                )
            ),
        ]
        return frames_to_gif(frames, fps=5)
    raw = (job.out_dir / f"raw-{stem}.png").read_bytes()
    mp4 = provider.animate(compile_motion(job.pack, job.pack.memes[pos]), raw)
    return mp4_to_wechat_gif(
        mp4, caption_source=(job.out_dir / f"{stem}.png").read_bytes()
    )


def _run_animate(job: Job, provider, index: int, mode: str) -> None:
    pos = index - 1
    try:
        gif = _make_anim_gif(job, index, mode, provider)
        stem = _sticker_stem(job, index)
        (job.out_dir / f"{stem}.anim.gif").write_bytes(gif)
        with job.lock:
            job.images[pos]["anim_status"] = "done"
            job.images[pos]["anim_url"] = f"/files/{job.id}/{stem}.anim.gif"
    except Exception as e:
        with job.lock:
            job.images[pos]["anim_status"] = "error"
        job.error = str(e)
    _save_meta(job)


def _run_retry(
    job: Job, provider: ImageProvider, index: int, caption: str | None = None
) -> None:
    pos = index - 1
    try:
        meme = job.pack.memes[pos]
        prompt = compile_meme(job.pack, meme, caption_override=caption)
        raw = provider.generate(prompt, job.selfie)
        _write_one(job, index, raw)
        with job.lock:
            job.images[pos]["status"] = "done"
        _rebuild_collage(job)
        job.status = "done"
    except Exception as e:
        with job.lock:
            job.images[pos]["status"] = "error"
        job.status = "done"
        job.error = str(e)
    _save_meta(job)


def _job_json(job: Job) -> dict:
    with job.lock:
        return {
            "job_id": job.id,
            "status": job.status,
            "error": job.error,
            "pack_name": job.pack_name,
            "created_at": job.created_at,
            "total": len(job.images),
            "images": [dict(img) for img in job.images],
            "collage_url": job.collage_url,
        }


def create_app() -> FastAPI:
    app = FastAPI(title="mememe")
    jobs: dict[str, Job] = _load_jobs()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/api/packs")
    def list_packs() -> list[dict]:
        packs = []
        for path in sorted(PACKS_DIR.glob("*.yaml")):
            pack = load_pack(path)
            has_preview = (PACKS_DIR / "previews" / f"{pack.id}.png").exists()
            packs.append(
                {
                    "id": pack.id,
                    "name": pack.name,
                    "description": pack.description,
                    "meme_count": len(pack.memes),
                    "captions": [m.caption for m in pack.memes],
                    "preview_url": f"/api/pack-preview/{pack.id}" if has_preview else "",
                }
            )
        # 旗舰在前，新投稿的包按字母序排在后面
        order = {"shechu": 0, "yinyang": 1, "lianai": 2, "ganfan": 3, "qimo": 4, "hajimi": 5}
        packs.sort(key=lambda p: (order.get(p["id"], 99), p["id"]))
        return packs

    @app.get("/api/pack-preview/{pack_id}")
    def pack_preview(pack_id: str) -> FileResponse:
        if "/" in pack_id or ".." in pack_id:
            raise HTTPException(404, "not found")
        path = PACKS_DIR / "previews" / f"{pack_id}.png"
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/generate")
    def generate(
        selfie: UploadFile = File(...),
        pack_id: str = Form(...),
        full: bool = Form(False),
        provider: str = Form(""),
    ) -> dict:
        pack_path = PACKS_DIR / f"{pack_id}.yaml"
        if not pack_path.exists():
            raise HTTPException(404, f"pack not found: {pack_id}")
        pack = load_pack(pack_path)
        job_id = uuid.uuid4().hex[:12]
        out_dir = OUTPUT_ROOT / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=job_id,
            pack=pack,
            selfie=selfie.file.read(),
            out_dir=out_dir,
            full=full,
            pack_id=pack_id,
            pack_name=pack.name,
            created_at=time.time(),
            provider_name=provider,
        )
        job.images = [
            {
                "index": i + 1,
                "id": m.id,
                "caption": m.caption,
                "status": "pending",
                "url": "",
                "gif_url": "",
                "anim_status": "none",
                "anim_url": "",
            }
            for i, m in enumerate(job.memes)
        ]
        jobs[job_id] = job
        _save_meta(job)
        threading.Thread(
            target=_run_generation, args=(job, _make_provider(provider)), daemon=True
        ).start()
        return {"job_id": job_id}

    @app.get("/api/history")
    def history() -> list[dict]:
        items = []
        for job in jobs.values():
            done = sum(1 for i in job.images if i["status"] == "done")
            first = next((i["url"] for i in job.images if i.get("url")), "")
            items.append(
                {
                    "job_id": job.id,
                    "pack_name": job.pack_name,
                    "created_at": job.created_at,
                    "total": len(job.images),
                    "done": done,
                    "collage_url": job.collage_url,
                    "thumb": job.collage_url or first,
                }
            )
        items.sort(key=lambda x: x["created_at"], reverse=True)
        return items

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return _job_json(job)

    @app.post("/api/jobs/{job_id}/retry/{index}")
    def retry(job_id: str, index: int, caption: str = Form("")) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if not job.selfie or job.pack is None:
            raise HTTPException(
                409, "历史任务的自拍已按隐私策略删除，无法重摇；想换就重新生成一套"
            )
        if job.status == "running":
            raise HTTPException(409, "job still running")
        if not 1 <= index <= len(job.images):
            raise HTTPException(400, f"index must be 1..{len(job.images)}")
        with job.lock:
            job.status = "running"
            job.images[index - 1]["status"] = "running"
        threading.Thread(
            target=_run_retry,
            args=(job, _make_provider(job.provider_name), index, caption or None),
            daemon=True,
        ).start()
        return {"job_id": job_id}

    @app.post("/api/jobs/{job_id}/animate/{index}")
    def animate(job_id: str, index: int, mode: str = Form("video")) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if mode not in ("video", "frames", "shake", "bounce"):
            raise HTTPException(400, f"unknown mode: {mode}")
        if mode in ("video", "frames") and job.pack is None:
            raise HTTPException(409, "找不到该任务的剧本文件，只能用「抖一抖」")
        if not 1 <= index <= len(job.images):
            raise HTTPException(400, f"index must be 1..{len(job.images)}")
        if mode in ("video", "frames"):
            stem = _sticker_stem(job, index)
            if not (job.out_dir / f"raw-{stem}.png").exists():
                raise HTTPException(
                    409, "这个历史任务没有保存原图，拼帧和视频做不了，只能用「抖一抖」"
                )
        with job.lock:
            img = job.images[index - 1]
            if img["status"] != "done":
                raise HTTPException(409, "sticker not ready")
            if img["anim_status"] == "running":
                raise HTTPException(409, "already animating")
            img["anim_status"] = "running"
        if mode == "video":
            provider = _make_video_provider()
        elif mode == "frames":
            provider = _make_provider(job.provider_name)
        else:
            provider = None
        threading.Thread(
            target=_run_animate, args=(job, provider, index, mode), daemon=True
        ).start()
        return {"job_id": job_id}

    @app.get("/api/lan-qr")
    def lan_qr(request: Request) -> Response:
        import io as _io
        import socket

        import qrcode

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        port = request.url.port or 80
        url = f"http://{ip}:{port}/"
        qr = qrcode.make(url)
        buf = _io.BytesIO()
        qr.save(buf, format="PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"X-Lan-Url": url},
        )

    @app.get("/files/{job_id}/{filename}")
    def files(job_id: str, filename: str) -> FileResponse:
        job = jobs.get(job_id)
        if job is None or "/" in filename or ".." in filename:
            raise HTTPException(404, "not found")
        path = job.out_dir / filename
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path)

    return app


_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>表情包工厂 · mememe</title>
<style>
  * { box-sizing: border-box; font-family: -apple-system, "PingFang SC", sans-serif; margin: 0; }
  body { background: #faf8f5; color: #1d1d1f; }
  .wrap { max-width: 600px; margin: 0 auto; padding: 0 16px 80px; }
  .hero { text-align: center; padding: 40px 16px 28px;
          background: linear-gradient(160deg, #fff7ed 0%, #ffe8d6 55%, #ffd9c0 100%);
          border-radius: 0 0 28px 28px; margin: 0 -16px 20px; }
  .hero h1 { font-size: 32px; letter-spacing: 1px; }
  .hero .tag { color: #8a6a52; font-size: 15px; margin-top: 8px; }
  .steps { display: flex; justify-content: center; gap: 18px; margin-top: 18px;
           font-size: 13px; color: #a07; color: #9a7b62; }
  .steps span { background: #ffffffaa; border-radius: 999px; padding: 6px 14px; }
  .card { background: #fff; border: 1px solid #eee4da; border-radius: 16px;
          padding: 18px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(60,40,20,.04); }
  .label { font-size: 13px; color: #b09880; font-weight: 600; margin-bottom: 10px; letter-spacing: .5px; }
  .drop { border: 2px dashed #dcc9b6; border-radius: 14px; padding: 26px; text-align: center;
          cursor: pointer; color: #8a6a52; transition: .15s; }
  .drop:hover { border-color: #c97b4a; }
  .drop.has { border-style: solid; border-color: #2a9d5c; color: #2a9d5c; }
  .drop img { max-height: 110px; border-radius: 10px; display: block; margin: 0 auto 10px; }
  .privacy { font-size: 12px; color: #bbb; text-align: center; margin-top: 8px; }
  .hist { cursor: grab; user-select: none; -webkit-user-select: none; }
  .hist:active { cursor: grabbing; }
  .hist img { -webkit-user-drag: none; }
  .packsearch { width: 100%; border: 1.5px solid #e5d8ca; border-radius: 10px;
                padding: 10px 12px; font-size: 14px; margin-bottom: 12px;
                background: #fffdfa; outline: none; }
  .packsearch:focus { border-color: #c97b4a; }
  .packgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  @media (max-width: 560px) { .packgrid { grid-template-columns: repeat(2, 1fr); } }
  .pcard { border: 2px solid #eee0d2; border-radius: 14px; padding: 10px;
           cursor: pointer; transition: .15s; background: #fffdfa; }
  .pcard:hover { border-color: #c97b4a; }
  .pcard.sel { border-color: #c9551e; background: #fff3ea; }
  .pcard img { width: 100%; aspect-ratio: 1; object-fit: contain;
               border-radius: 10px; background: #f6f2ec; }
  .pname { font-size: 14px; font-weight: 700; margin-top: 8px;
           display: flex; justify-content: space-between; align-items: baseline; }
  .pcount { font-size: 10px; color: #b3a392; font-weight: 400; white-space: nowrap; }
  .pdesc { font-size: 11px; color: #a89b8d; margin-top: 3px; line-height: 1.5; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
  .chips span { font-size: 10px; background: #f3e9de; color: #8a6a52;
                border-radius: 6px; padding: 2px 6px; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  select { border: 1px solid #ddd; border-radius: 8px; padding: 6px 8px; font-size: 13px; background:#fff; }
  button.go { flex: 1; min-width: 200px; background: linear-gradient(135deg,#e2622b,#c9551e);
              color: #fff; border: 0; border-radius: 999px; padding: 15px;
              font-size: 16px; font-weight: 700; cursor: pointer; }
  button.go:disabled { background: #d9cdc2; cursor: not-allowed; }
  .toggle { font-size: 13px; color: #6f5d4d; white-space: nowrap; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  @media (max-width: 430px) { .grid { grid-template-columns: repeat(3, 1fr); } }
  .cell { aspect-ratio: 1; border: 1px solid #efe5da; border-radius: 12px; position: relative;
          display: flex; align-items: center; justify-content: center; overflow: hidden; background: #f6f2ec; }
  .cell img { width: 100%; height: 100%; object-fit: contain; animation: pop .3s ease; }
  @keyframes pop { from { transform: scale(.6); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  .cap { position: absolute; bottom: 5px; left: 0; right: 0; text-align: center;
         font-size: 11px; color: #b3a392; pointer-events: none; }
  .spin { width: 22px; height: 22px; border: 3px solid #e8ddd0; border-top-color: #c9551e;
          border-radius: 50%; animation: r 1s linear infinite; }
  @keyframes r { to { transform: rotate(360deg); } }
  .act { position: absolute; top: 4px; font-size: 12px; background: #fffffff0; border: 1px solid #e5d8ca;
         border-radius: 8px; padding: 3px 7px; cursor: pointer; line-height: 1; }
  .act.redo { left: 4px; }
  .act.anim { right: 4px; }
  .badge { position: absolute; top: 4px; right: 4px; font-size: 10px; font-weight: 700;
           background: #c9551e; color: #fff; border-radius: 6px; padding: 3px 6px; pointer-events:none; }
  .badge.busy { background: #8a6a52; animation: blink 1.2s ease infinite; }
  @keyframes blink { 50% { opacity: .45; } }
  .menu { position: absolute; inset: auto 4px 4px 4px; background: #fffffffa;
          border: 1px solid #e5d8ca; border-radius: 10px; z-index: 5;
          display: flex; flex-direction: column; overflow: hidden; }
  .menu button { border: 0; background: none; padding: 7px 8px; font-size: 11px;
                 cursor: pointer; text-align: left; white-space: nowrap; }
  .menu button:hover { background: #fff3ea; }
  .menu button + button { border-top: 1px solid #f3e9de; }
  .collage img { width: 100%; border-radius: 14px; border: 1px solid #eee4da; }
  .hint { font-size: 13px; color: #998a7a; line-height: 1.8; margin-top: 10px; }
  .err { color: #c0392b; font-size: 13px; white-space: pre-wrap; margin-top: 8px; }
  .hist { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
  .hitem { min-width: 116px; max-width: 116px; cursor: pointer; text-align: center;
           font-size: 12px; color: #6f5d4d; }
  .hitem img { width: 116px; height: 116px; object-fit: cover; border-radius: 10px;
               border: 1px solid #eee4da; background: #f6f2ec; }
  .hitem small { display: block; color: #b3a392; font-size: 10px; margin-top: 2px; }
  .qrbox { display: none; text-align: center; }
  .qrbox img { width: 132px; height: 132px; }
  @media (min-width: 700px) { .qrbox.lan { display: block; } }
  footer { text-align: center; color: #c7b9aa; font-size: 12px; margin-top: 28px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>表情包工厂</h1>
    <div class="tag">一张自拍，变成一整套微信表情包</div>
    <div class="steps"><span>① 上传自拍</span><span>② 选梗剧本</span><span>③ 逐张揭晓</span></div>
  </div>

  <div class="card">
    <div class="label">1 · 上传一张正脸自拍</div>
    <div class="drop" id="drop" onclick="document.getElementById('file').click()">📷 点击选择照片</div>
    <input type="file" id="file" accept="image/*" hidden>
    <div class="privacy">照片只存在内存里，服务停止即消失</div>
  </div>

  <div class="card">
    <div class="label">2 · 选梗剧本</div>
    <input class="packsearch" id="psearch" type="search"
           placeholder="🔍 搜剧本或梗：摸鱼 / 阴阳 / 亲亲 / 求过……">
    <div class="packgrid" id="packs"></div>
  </div>

  <div class="card">
    <div class="label">3 · 生成</div>
    <div class="row" style="margin-bottom:12px">
      <label class="toggle">模型
        <select id="prov">
          <option value="gemini">Gemini（中转）</option>
          <option value="seedream">即梦 Seedream</option>
        </select>
      </label>
      <label class="toggle"><input type="checkbox" id="full"> 全套16张</label>
    </div>
    <div class="row"><button class="go" id="go" disabled>生成我的表情包</button></div>
    <div class="err" id="err"></div>
  </div>

  <div class="card" id="resultCard" style="display:none">
    <div class="label">4 · 逐张揭晓</div>
    <div class="grid" id="grid"></div>
    <div class="hint">🔄 重摇换一张（可改文案）｜✨ 让它动起来（约 1-2 分钟）<br>
    手机上：长按图片保存 → 微信里发给自己 → 长按「添加到表情」</div>
  </div>

  <div class="card collage" id="collageCard" style="display:none">
    <div class="label">5 · 合集晒图卡（发朋友圈用这张）</div>
    <img id="collageImg">
  </div>

  <div class="card" id="histCard" style="display:none">
    <div class="label">历史生成（点击查看）</div>
    <div class="hist" id="hist"></div>
  </div>

  <div class="card qrbox" id="qrbox">
    <div class="label">📱 手机扫码打开本页，长按直接保存</div>
    <img src="/api/lan-qr" onload="this.parentElement.classList.add('lan')" onerror="this.parentElement.style.display='none'">
  </div>

  <footer>mememe · 本地运行 · Apache-2.0</footer>
</div>

<script>
let selfie = null, packId = null, jobId = null, timer = null;
const $ = (id) => document.getElementById(id);

let allPacks = [];

function renderPacks(filter) {
  const box = $('packs');
  const kw = (filter || '').trim().toLowerCase();
  const shown = allPacks.filter(p => !kw
    || p.name.toLowerCase().includes(kw)
    || (p.description || '').toLowerCase().includes(kw)
    || p.captions.some(c => c.toLowerCase().includes(kw)));
  if (!shown.find(p => p.id === packId)) packId = shown.length ? shown[0].id : null;
  box.innerHTML = '';
  if (!shown.length) {
    box.innerHTML = '<div class="hint">没有匹配的剧本——欢迎去 GitHub 投稿一套 🙌</div>';
    return;
  }
  shown.forEach(p => {
    const div = document.createElement('div');
    div.className = 'pcard' + (p.id === packId ? ' sel' : '');
    const chips = p.captions.slice(0, 3).map(c => `<span>${c}</span>`).join('')
      + `<span>+${p.meme_count - 3}</span>`;
    div.innerHTML = `${p.preview_url ? `<img src="${p.preview_url}" loading="lazy">` : ''}
      <div class="pname">${p.name}<span class="pcount">${p.meme_count}梗</span></div>
      <div class="pdesc">${p.description || ''}</div>
      <div class="chips">${chips}</div>`;
    div.onclick = () => { packId = p.id; renderPacks($('psearch').value); };
    box.appendChild(div);
  });
}

fetch('/api/packs').then(r => r.json()).then(packs => {
  allPacks = packs;
  renderPacks('');
});
$('psearch').oninput = (e) => renderPacks(e.target.value);

$('file').onchange = (e) => {
  selfie = e.target.files[0];
  if (!selfie) return;
  const drop = $('drop');
  drop.classList.add('has');
  drop.innerHTML = `<img src="${URL.createObjectURL(selfie)}">已选择，点击可更换`;
  $('go').disabled = false;
};

$('go').onclick = async () => {
  $('go').disabled = true; $('err').textContent = '';
  const fd = new FormData();
  fd.append('selfie', selfie);
  fd.append('pack_id', packId);
  fd.append('full', $('full').checked);
  fd.append('provider', $('prov').value);
  const resp = await fetch('/api/generate', { method: 'POST', body: fd });
  if (!resp.ok) { $('err').textContent = await resp.text(); $('go').disabled = false; return; }
  jobId = (await resp.json()).job_id;
  $('resultCard').style.display = 'block';
  $('collageCard').style.display = 'none';
  $('grid').innerHTML = '';
  startPoll();
};

function startPoll() { if (timer) clearInterval(timer); timer = setInterval(poll, 1000); }

async function poll() {
  const resp = await fetch(`/api/jobs/${jobId}`);
  if (!resp.ok) return;
  const job = await resp.json();
  render(job);
  const busy = job.status === 'running' || job.images.some(i => i.anim_status === 'running');
  if (!busy) {
    if (timer) { clearInterval(timer); timer = null; loadHistory(); }
    $('go').disabled = false;
    if (job.error) $('err').textContent = job.error;
  }
  if (job.collage_url) { $('collageImg').src = job.collage_url + '?t=' + (job.status==='running'?0:Date.now()); $('collageCard').style.display = 'block'; }
}

function cellState(img) { return img.status + ':' + img.anim_status; }

function render(job) {
  const grid = $('grid');
  if (grid.children.length !== job.images.length) {
    grid.innerHTML = '';
    job.images.forEach(img => {
      const cell = document.createElement('div');
      cell.className = 'cell'; cell.id = 'cell-' + img.index;
      grid.appendChild(cell);
    });
  }
  job.images.forEach(img => {
    const cell = $('cell-' + img.index);
    const state = cellState(img);
    if (cell.dataset.state === state) return;
    cell.dataset.state = state;
    if (img.status === 'pending') {
      cell.innerHTML = `<div class="cap">${img.caption}</div>`;
    } else if (img.status === 'running') {
      cell.innerHTML = `<div class="spin"></div><div class="cap">${img.caption}</div>`;
    } else if (img.status === 'error') {
      cell.innerHTML = `<div class="cap">失败</div><div class="act redo">🔄</div>`;
      cell.querySelector('.redo').onclick = () => retry(img.index);
    } else if (img.anim_status === 'done') {
      cell.innerHTML = `<img src="${img.anim_url}?t=${Date.now()}"><div class="badge">GIF</div>
        <div class="act anim" style="top:auto;bottom:4px" title="换一种动法">✨</div>`;
      cell.querySelector('.anim').onclick = (e) => { e.stopPropagation(); animMenu(img.index); };
    } else if (img.anim_status === 'running') {
      cell.innerHTML = `<img src="${img.url}"><div class="badge busy">🎬 动图中</div>`;
    } else {
      const animBtn = img.anim_status === 'error' ? '✨重试' : '✨';
      cell.innerHTML = `<img src="${img.url}?t=${Date.now()}">
        <div class="act redo" title="重摇">🔄</div>
        <div class="act anim" title="动起来">${animBtn}</div>`;
      cell.querySelector('.redo').onclick = (e) => { e.stopPropagation(); retry(img.index); };
      cell.querySelector('.anim').onclick = (e) => { e.stopPropagation(); animMenu(img.index); };
    }
  });
}

async function retry(index) {
  const text = prompt('想换的文案？留空保持原文案（也可以只重摇不改字）', '');
  if (text === null) return;
  const cell = $('cell-' + index);
  cell.dataset.state = ''; cell.innerHTML = '<div class="spin"></div>';
  const fd = new FormData();
  if (text.trim()) fd.append('caption', text.trim());
  await fetch(`/api/jobs/${jobId}/retry/${index}`, { method: 'POST', body: fd });
  startPoll();
}

function animMenu(index) {
  const cell = $('cell-' + index);
  const old = cell.querySelector('.menu');
  if (old) { old.remove(); return; }
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.innerHTML = `
    <button data-m="shake">⚡ 抖一抖 · 免费秒出</button>
    <button data-m="frames">🎞 拼帧 · 几分钱 ~30s</button>
    <button data-m="video">🎬 视频 · 最贵 ~2分钟</button>`;
  menu.querySelectorAll('button').forEach(b => {
    b.onclick = (e) => { e.stopPropagation(); menu.remove(); animate(index, b.dataset.m); };
  });
  cell.appendChild(menu);
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 0);
}

async function animate(index, mode) {
  const fd = new FormData();
  fd.append('mode', mode);
  const resp = await fetch(`/api/jobs/${jobId}/animate/${index}`, { method: 'POST', body: fd });
  if (!resp.ok) { $('err').textContent = await resp.text(); return; }
  startPoll();
}

async function loadHistory() {
  const items = await (await fetch('/api/history')).json();
  if (!items.length) return;
  $('histCard').style.display = 'block';
  const box = $('hist');
  box.innerHTML = '';
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'hitem';
    const when = it.created_at ? new Date(it.created_at * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
    div.innerHTML = `<img src="${it.thumb || ''}">${it.pack_name}<small>${when} · ${it.done}/${it.total}张</small>`;
    div.onclick = () => openJob(it.job_id);
    box.appendChild(div);
  });
}

function openJob(id) {
  jobId = id;
  $('resultCard').style.display = 'block';
  $('collageCard').style.display = 'none';
  $('grid').innerHTML = '';
  $('err').textContent = '';
  startPoll();
  window.scrollTo({ top: $('resultCard').offsetTop - 12, behavior: 'smooth' });
}

function makeDraggable(el) {
  let down = false, startX = 0, startLeft = 0, moved = false;
  el.addEventListener('pointerdown', (e) => {
    down = true; moved = false; startX = e.clientX; startLeft = el.scrollLeft;
    el.setPointerCapture(e.pointerId);
  });
  el.addEventListener('pointermove', (e) => {
    if (!down) return;
    const dx = e.clientX - startX;
    if (Math.abs(dx) > 5) { moved = true; el.scrollLeft = startLeft - dx; }
  });
  ['pointerup', 'pointercancel'].forEach(ev => el.addEventListener(ev, () => { down = false; }));
  el.addEventListener('click', (e) => {
    if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; }
  }, true);
  el.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { el.scrollLeft += e.deltaY; e.preventDefault(); }
  }, { passive: false });
}
makeDraggable($('hist'));

loadHistory();
</script>
</body>
</html>
"""
