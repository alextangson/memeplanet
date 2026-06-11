"""Local web UI — FastAPI wrapping core. The Stage 2b service grows from here.

Privacy: the selfie lives in process memory only (retry needs it); it is never
written to disk and vanishes when the server stops.
"""

import importlib.util
import json
import os

import yaml
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
LEADS_FILE = Path("out/leads.jsonl")
PACKS_DIR = Path(os.environ.get("MEMEME_PACKS_DIR", "packs"))
CUSTOM_PACKS_DIR = Path(os.environ.get("MEMEME_CUSTOM_PACKS_DIR", "packs/custom"))
DEFAULT_QR_URL = "https://github.com/alextangson/meme-me"


def _make_scriptwriter():
    from mememe.core.scriptwriter import Scriptwriter
    from mememe.providers.deepseek import DeepSeekChat

    return Scriptwriter(DeepSeekChat())


def _make_t2i():
    from mememe.providers.seedream import SeedreamProvider

    return SeedreamProvider().generate_text


def _generate_custom_preview(pack, path: Path) -> None:
    """定制包没有用户照片，用主角描述文生图出一张风格预览。失败静默（占位符兜底）。"""
    try:
        meme = pack.memes[0]
        prompt = (
            f"生成一张微信表情包贴纸。主角：{pack.subject_desc or pack.description}。\n"
            f"【画幅】正方形 1:1，背景必须纯白。\n【风格】\n{pack.style.strip()}\n"
            f"【内容】表情：{meme.expression}；动作：{meme.action}；"
            f"画面文案（渲染在图内下方）：「{meme.caption}」"
        )
        raw = _make_t2i()(prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(to_sticker_png(raw))
    except Exception:
        pass


def _find_pack_path(pack_id: str) -> Path | None:
    if "/" in pack_id or ".." in pack_id:
        return None
    for base in (PACKS_DIR, CUSTOM_PACKS_DIR):
        path = base / f"{pack_id}.yaml"
        if path.exists():
            return path
    return None


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
    style: str = ""
    caption_style: str = ""
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
            "style": job.style,
            "caption_style": job.caption_style,
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
        pack_file = _find_pack_path(meta.get("pack_id", ""))
        if pack_file is not None:
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
            style=meta.get("style", ""),
            caption_style=meta.get("caption_style", ""),
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
            raw = provider.generate(
                compile_meme(
                    job.pack, meme, style=job.style, caption_style=job.caption_style
                ),
                job.selfie,
            )
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


def _make_anim_gif(
    job: Job, index: int, mode: str, provider, motion: str | None = None
) -> bytes:
    pos = index - 1
    stem = _sticker_stem(job, index)
    if mode in ("shake", "bounce"):
        png = (job.out_dir / f"{stem}.png").read_bytes()
        return procedural_gif(png, effect=mode)
    if mode == "frames":
        raw = (job.out_dir / f"raw-{stem}.png").read_bytes()
        prompt = compile_keyframe(job.pack, job.pack.memes[pos], motion_override=motion)
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
    mp4 = provider.animate(
        compile_motion(job.pack, job.pack.memes[pos], motion_override=motion), raw
    )
    return mp4_to_wechat_gif(
        mp4, caption_source=(job.out_dir / f"{stem}.png").read_bytes()
    )


def _run_animate(
    job: Job, provider, index: int, mode: str, motion: str | None = None
) -> None:
    pos = index - 1
    try:
        gif = _make_anim_gif(job, index, mode, provider, motion)
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


def _run_extend(job: Job, provider: ImageProvider) -> None:
    try:
        for pos in range(8, len(job.pack.memes)):
            index = pos + 1
            meme = job.pack.memes[pos]
            with job.lock:
                job.images[pos]["status"] = "running"
            raw = provider.generate(
                compile_meme(
                    job.pack, meme, style=job.style, caption_style=job.caption_style
                ),
                job.selfie,
            )
            _write_one(job, index, raw)
            with job.lock:
                job.images[pos]["status"] = "done"
                job.images[pos]["url"] = f"/files/{job.id}/{_sticker_stem(job, index)}.png"
                job.images[pos]["gif_url"] = f"/files/{job.id}/{_sticker_stem(job, index)}.gif"
        job.status = "done"
    except Exception as e:
        job.status = "error"
        job.error = str(e)
    _save_meta(job)


def _run_retry(
    job: Job, provider: ImageProvider, index: int, caption: str | None = None
) -> None:
    pos = index - 1
    try:
        meme = job.pack.memes[pos]
        prompt = compile_meme(
            job.pack,
            meme,
            caption_override=caption,
            style=job.style,
            caption_style=job.caption_style,
        )
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

    @app.get("/custom", response_class=HTMLResponse)
    def custom_page() -> str:
        return _CUSTOM_HTML

    @app.post("/api/leads")
    def leads(contact: str = Form(...), need: str = Form("")) -> dict:
        LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LEADS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"contact": contact, "need": need, "ts": time.time()},
                ensure_ascii=False,
            ) + "\n")
        return {"ok": True}

    @app.get("/api/packs")
    def list_packs() -> list[dict]:
        packs = []
        sources = [(PACKS_DIR, False)]
        if CUSTOM_PACKS_DIR.exists():
            sources.append((CUSTOM_PACKS_DIR, True))
        for base, is_custom in sources:
            for path in sorted(base.glob("*.yaml")):
                pack = load_pack(path)
                has_preview = any(
                    (b / "previews" / f"{pack.id}.png").exists()
                    for b in (PACKS_DIR, CUSTOM_PACKS_DIR)
                )
                packs.append(
                    {
                        "id": pack.id,
                        "name": pack.name,
                        "description": pack.description,
                        "meme_count": len(pack.memes),
                        "captions": [m.caption for m in pack.memes],
                        "preview_url": f"/api/pack-preview/{pack.id}" if has_preview else "",
                        "custom": is_custom,
                    }
                )
        # 自己的定制包最前，旗舰其次，新投稿按字母序殿后
        order = {"shechu": 0, "qinglv": 1, "maomi": 2, "gouzi": 3, "yinyang": 4, "lianai": 5, "ganfan": 6, "qimo": 7, "hajimi": 8}
        packs.sort(key=lambda p: (not p["custom"], order.get(p["id"], 99), p["id"]))
        return packs

    @app.get("/api/styles")
    def list_styles() -> dict:
        from mememe.core.styles import CAPTION_STYLES, STYLES

        return {
            "styles": [
                {"id": k, "name": v["name"], "desc": v["desc"]}
                for k, v in STYLES.items()
            ],
            "caption_styles": [
                {"id": k, "name": v["name"], "desc": v["desc"]}
                for k, v in CAPTION_STYLES.items()
            ],
        }

    drafts: dict[str, list] = {}

    @app.post("/api/agent/chat")
    def agent_chat(message: str = Form(...), draft_id: str = Form("")) -> dict:
        if not draft_id:
            draft_id = uuid.uuid4().hex[:12]
            drafts[draft_id] = []
        history = drafts.get(draft_id)
        if history is None:
            raise HTTPException(404, "对话不存在，刷新页面重新开始")
        history.append({"role": "user", "content": message})
        try:
            reply = _make_scriptwriter().reply(history)
        except Exception as e:
            history.pop()
            raise HTTPException(502, f"策划暂时掉线了：{e}")
        history.append({"role": "assistant", "content": reply})
        return {"draft_id": draft_id, "reply": reply}

    @app.post("/api/agent/draft")
    def agent_draft(draft_id: str = Form(...)) -> dict:
        history = drafts.get(draft_id)
        if not history:
            raise HTTPException(404, "对话不存在，先聊两句再生成")
        try:
            pack = _make_scriptwriter().draft(history)
        except ValueError as e:
            raise HTTPException(422, str(e))
        CUSTOM_PACKS_DIR.mkdir(parents=True, exist_ok=True)
        pack_id = pack.id
        n = 2
        while (CUSTOM_PACKS_DIR / f"{pack_id}.yaml").exists():
            pack_id = f"{pack.id}-{n}"
            n += 1
        data = pack.model_dump()
        data["id"] = pack_id
        (CUSTOM_PACKS_DIR / f"{pack_id}.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        threading.Thread(
            target=_generate_custom_preview,
            args=(pack, CUSTOM_PACKS_DIR / "previews" / f"{pack_id}.png"),
            daemon=True,
        ).start()
        return {
            "pack_id": pack_id,
            "name": pack.name,
            "meme_count": len(pack.memes),
            "captions": [m.caption for m in pack.memes[:8]],
        }

    @app.get("/api/pack-preview/{pack_id}")
    def pack_preview(pack_id: str) -> FileResponse:
        if "/" in pack_id or ".." in pack_id:
            raise HTTPException(404, "not found")
        for base in (PACKS_DIR, CUSTOM_PACKS_DIR):
            path = base / "previews" / f"{pack_id}.png"
            if path.exists():
                return FileResponse(path, media_type="image/png")
        raise HTTPException(404, "not found")

    @app.post("/api/generate")
    def generate(
        selfie: UploadFile = File(...),
        pack_id: str = Form(...),
        full: bool = Form(False),
        provider: str = Form(""),
        style: str = Form(""),
        caption_style: str = Form(""),
    ) -> dict:
        pack_path = _find_pack_path(pack_id)
        if pack_path is None:
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
            style=style,
            caption_style=caption_style,
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
    def animate(
        job_id: str,
        index: int,
        mode: str = Form("video"),
        motion: str = Form(""),
    ) -> dict:
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
            target=_run_animate,
            args=(job, provider, index, mode, motion.strip() or None),
            daemon=True,
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

    @app.post("/api/jobs/{job_id}/extend")
    def extend(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if not job.selfie or job.pack is None:
            raise HTTPException(409, "原始照片已按隐私策略删除，重新生成一套即可直接选全套")
        if job.status == "running":
            raise HTTPException(409, "job still running")
        if job.full or len(job.images) >= len(job.pack.memes):
            raise HTTPException(409, "已经是全套了")
        with job.lock:
            job.full = True
            job.status = "running"
            for i, m in enumerate(job.pack.memes[8:], start=9):
                job.images.append(
                    {
                        "index": i, "id": m.id, "caption": m.caption,
                        "status": "pending", "url": "", "gif_url": "",
                        "anim_status": "none", "anim_url": "",
                    }
                )
        threading.Thread(
            target=_run_extend,
            args=(job, _make_provider(job.provider_name)),
            daemon=True,
        ).start()
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}/platform-pack")
    def platform_pack(job_id: str) -> Response:
        from mememe.core.platform import build_platform_zip

        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        stickers = []
        for i in range(1, len(job.images) + 1):
            path = job.out_dir / f"{_sticker_stem(job, i)}.png"
            if path.exists():
                stickers.append(path.read_bytes())
        if not stickers:
            raise HTTPException(409, "这套还没有生成完成的表情")
        blob = build_platform_zip(stickers, pack_name=job.pack_name or "表情包")
        return Response(
            content=blob,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="platform-{job_id}.zip"'
            },
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


_INDEX_HTML = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")
_CUSTOM_HTML = (Path(__file__).parent / "custom.html").read_text(encoding="utf-8")
