"""Local web UI — FastAPI wrapping core. The Stage 2b service grows from here.

Privacy: the selfie lives in process memory only (retry needs it); it is never
written to disk and vanishes when the server stops.
"""

import importlib.util
import hmac
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

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
    normalize_selfie,
    to_sticker_gif,
    to_sticker_png,
)
from mememe.core.schema import Pack, load_pack
from mememe.providers.base import ImageProvider

OUTPUT_ROOT = Path("out/web")
LEADS_FILE = Path("out/leads.jsonl")
EVENTS_FILE = Path("out/events.jsonl")  # 转化漏斗埋点（unlock_shown/unlock_free_click）
# B 端联系入口：放一张微信二维码图在这就会出现在 /custom 页（个人数据，不进 repo）
CONTACT_QR_FILE = Path(os.environ.get("MEMEME_CONTACT_QR", "out/contact-qr.png"))
PACKS_DIR = Path(os.environ.get("MEMEME_PACKS_DIR", "packs"))
CUSTOM_PACKS_DIR = Path(os.environ.get("MEMEME_CUSTOM_PACKS_DIR", "packs/custom"))
# 晒图卡二维码落地页；部署后设 MEMEME_QR_URL=https://meme-planet.com/
DEFAULT_QR_URL = os.environ.get("MEMEME_QR_URL", "https://github.com/alextangson/memeplanet")
MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # 单张上传上限，挡 OOM-by-upload
ADMIN_KEY = os.environ.get("MEMEME_ADMIN_KEY", "")  # 设了才开后台；/admin?key=
MAX_CONCURRENT_GENERATIONS = int(os.environ.get("MEMEME_MAX_CONCURRENT", "3"))
_GEN_SLOTS = threading.Semaphore(MAX_CONCURRENT_GENERATIONS)
DEFAULT_PROVIDER = os.environ.get("MEMEME_PROVIDER", "seedream")  # 即梦主力，省钱
GEN_FANOUT = int(os.environ.get("MEMEME_GEN_FANOUT", "4"))  # 单任务内并发出图张数


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
    name = name or DEFAULT_PROVIDER
    if name == "seedream":
        from mememe.providers.seedream import SeedreamProvider

        return SeedreamProvider()
    from mememe.providers.gemini import GeminiProvider

    return GeminiProvider()


def _fallback_image_provider(name: str) -> ImageProvider | None:
    """主力上游翻车（即梦 429/中转 500 等）时换另一家试一次。"""
    primary = name or DEFAULT_PROVIDER
    other = "gemini" if primary == "seedream" else "seedream"
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


def _generate_one(job: Job, provider: ImageProvider, pos: int) -> None:
    """生成单张：主力上游失败时自动换另一家补一次，不连累整套。"""
    index = pos + 1
    meme = job.pack.memes[pos]
    prompt = compile_meme(
        job.pack, meme, style=job.style, caption_style=job.caption_style
    )
    with job.lock:
        job.images[pos]["status"] = "running"
    try:
        try:
            raw = provider.generate(prompt, job.selfie)
        except Exception:
            fallback = _fallback_image_provider(job.provider_name)
            if fallback is None:
                raise
            raw = fallback.generate(prompt, job.selfie)
        _write_one(job, index, raw)
        with job.lock:
            job.images[pos]["status"] = "done"
            job.images[pos]["url"] = f"/files/{job.id}/{_sticker_stem(job, index)}.png"
            job.images[pos]["gif_url"] = f"/files/{job.id}/{_sticker_stem(job, index)}.gif"
    except Exception as e:  # 单张失败只记这张，其余照常；用户可重摇
        with job.lock:
            job.images[pos]["status"] = "error"
        job.error = str(e)


def _generate_batch(job: Job, provider: ImageProvider, positions: list[int]) -> None:
    """有界并发出图：快但不撞上游限流、不撑爆小内存。逐张完成、逐张揭晓。"""
    with ThreadPoolExecutor(max_workers=GEN_FANOUT) as pool:
        list(pool.map(lambda pos: _generate_one(job, provider, pos), positions))


def _any_done(job: Job) -> bool:
    return any(i["status"] == "done" for i in job.images)


def _run_generation(job: Job, provider: ImageProvider) -> None:
    _generate_batch(job, provider, list(range(len(job.memes))))
    _rebuild_collage(job)
    # 出了几张就算成功（单张错误可重摇）；一张都没出才算整套失败
    job.status = "done" if _any_done(job) else "error"
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
    _generate_batch(job, provider, list(range(8, len(job.pack.memes))))
    job.status = "done" if _any_done(job) else "error"
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


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:] if limit else rows


def _admin_data(jobs: dict[str, "Job"]) -> dict:
    events = _read_jsonl(EVENTS_FILE)
    funnel: dict[str, int] = {}
    errors = []
    for e in events:
        funnel[e["name"]] = funnel.get(e["name"], 0) + 1
        if e["name"].startswith(("js_error", "js_reject")) and e.get("detail"):
            errors.append({"detail": e["detail"], "ts": e.get("ts", 0)})

    by_status: dict[str, int] = {}
    by_pack: dict[str, int] = {}
    recent = []
    for job in jobs.values():
        by_status[job.status] = by_status.get(job.status, 0) + 1
        by_pack[job.pack_name or "?"] = by_pack.get(job.pack_name or "?", 0) + 1
    for job in sorted(jobs.values(), key=lambda j: j.created_at, reverse=True)[:30]:
        done = sum(1 for i in job.images if i["status"] == "done")
        recent.append({
            "job_id": job.id, "pack_name": job.pack_name, "status": job.status,
            "done": done, "total": len(job.images), "created_at": job.created_at,
            "error": job.error,
        })

    return {
        "leads": list(reversed(_read_jsonl(LEADS_FILE))),
        "funnel": funnel,
        "errors": sorted(errors, key=lambda x: x["ts"], reverse=True)[:50],
        "jobs": {"total": len(jobs), "by_status": by_status, "by_pack": by_pack, "recent": recent},
    }


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

    # no-cache：否则浏览器启发式缓存让改版后的页面迟迟到不了用户手里
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML, headers={"Cache-Control": "no-cache"})

    @app.get("/custom", response_class=HTMLResponse)
    def custom_page() -> HTMLResponse:
        return HTMLResponse(_CUSTOM_HTML, headers={"Cache-Control": "no-cache"})

    @app.post("/api/leads")
    def leads(contact: str = Form(...), need: str = Form("")) -> dict:
        LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LEADS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"contact": contact, "need": need, "ts": time.time()},
                ensure_ascii=False,
            ) + "\n")
        return {"ok": True}

    def _pack_info(path: Path, is_custom: bool) -> dict:
        pack = load_pack(path)
        has_preview = any(
            (b / "previews" / f"{pack.id}.png").exists()
            for b in (PACKS_DIR, CUSTOM_PACKS_DIR)
        )
        return {
            "id": pack.id,
            "name": pack.name,
            "description": pack.description,
            "meme_count": len(pack.memes),
            "captions": [m.caption for m in pack.memes],
            "preview_url": f"/api/pack-preview/{pack.id}" if has_preview else "",
            "custom": is_custom,
        }

    @app.post("/api/events")
    def events(
        name: str = Form(...), job_id: str = Form(""), detail: str = Form("")
    ) -> dict:
        if not re.fullmatch(r"[a-z0-9_]{1,64}", name):
            raise HTTPException(422, "bad event name")
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {"name": name, "job_id": job_id, "ts": time.time()}
        if detail:
            row["detail"] = detail[:500]  # 客户端报错/上下文，截断防滥用
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True}

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> HTMLResponse:
        if not ADMIN_KEY:
            raise HTTPException(404, "not found")
        return HTMLResponse(_ADMIN_HTML, headers={"Cache-Control": "no-cache"})

    @app.get("/api/admin/data")
    def admin_data(key: str = "") -> dict:
        if not ADMIN_KEY:
            raise HTTPException(404, "not found")
        if not hmac.compare_digest(key, ADMIN_KEY):
            raise HTTPException(403, "forbidden")
        return _admin_data(jobs)

    @app.get("/api/packs")
    def list_packs() -> list[dict]:
        # 公共列表只含官方剧本；定制剧本仅创建者凭链接经 /api/packs/{id} 取
        packs = [_pack_info(p, False) for p in sorted(PACKS_DIR.glob("*.yaml"))]
        # 旗舰最前，新投稿按字母序殿后
        order = {"shechu": 0, "qinglv": 1, "maomi": 2, "gouzi": 3, "yinyang": 4, "lianai": 5, "ganfan": 6, "qimo": 7, "hajimi": 8}
        packs.sort(key=lambda p: (order.get(p["id"], 99), p["id"]))
        return packs

    @app.get("/api/packs/{pack_id}")
    def get_pack(pack_id: str) -> dict:
        path = _find_pack_path(pack_id)
        if path is None:
            raise HTTPException(404, "pack not found")
        return _pack_info(path, path.parent == CUSTOM_PACKS_DIR)

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

    @app.get("/logo.png")
    def logo() -> FileResponse:
        return FileResponse(
            Path(__file__).parent / "logo.png",
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/contact-qr")
    def contact_qr() -> FileResponse:
        if not CONTACT_QR_FILE.exists():
            raise HTTPException(404, "not found")
        return FileResponse(CONTACT_QR_FILE, media_type="image/png")

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
        raw_upload = selfie.file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw_upload) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "照片太大了，请压缩到 12MB 以内再试")
        try:
            selfie_bytes = normalize_selfie(raw_upload)
        except ValueError:
            raise HTTPException(400, "这看起来不是一张图片，换张照片试试")
        # 名额满了就婉拒，别让并发把 1G 小机撑爆
        if not _GEN_SLOTS.acquire(blocking=False):
            raise HTTPException(429, "正在帮前面的小伙伴生成，请过一会儿再试～")
        pack = load_pack(pack_path)
        job_id = uuid.uuid4().hex[:12]
        out_dir = OUTPUT_ROOT / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=job_id,
            pack=pack,
            selfie=selfie_bytes,
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

        def _run_and_release() -> None:
            try:
                _run_generation(job, _make_provider(provider))
            finally:
                _GEN_SLOTS.release()

        threading.Thread(target=_run_and_release, daemon=True).start()
        return {"job_id": job_id}

    @app.get("/api/history")
    def history() -> list[dict]:
        items = []
        for job in jobs.values():
            done = sum(1 for i in job.images if i["status"] == "done")
            if done == 0:
                continue  # 一张都没出（失败/刚启动）→ 无缩略图可显，不进历史
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
        anim_gifs: list[bytes | None] = []
        for i in range(1, len(job.images) + 1):
            stem = _sticker_stem(job, i)
            path = job.out_dir / f"{stem}.png"
            if path.exists():
                stickers.append(path.read_bytes())
                anim = job.out_dir / f"{stem}.anim.gif"
                anim_gifs.append(anim.read_bytes() if anim.exists() else None)
        if not stickers:
            raise HTTPException(409, "这套还没有生成完成的表情")
        blob = build_platform_zip(
            stickers, pack_name=job.pack_name or "表情包", anim_gifs=anim_gifs
        )
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
_ADMIN_HTML = (Path(__file__).parent / "admin.html").read_text(encoding="utf-8")
