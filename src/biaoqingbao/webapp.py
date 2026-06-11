"""Local web UI — FastAPI wrapping core. The Stage 2b service grows from here.

Privacy: the selfie lives in process memory only (retry needs it); it is never
written to disk and vanishes when the server stops.
"""

import importlib.util
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from biaoqingbao.core.collage import build_collage
from biaoqingbao.core.compiler import compile_meme
from biaoqingbao.core.postprocess import (
    maybe_remove_background,
    to_sticker_gif,
    to_sticker_png,
)
from biaoqingbao.core.schema import Pack, load_pack
from biaoqingbao.providers.base import ImageProvider

OUTPUT_ROOT = Path("out/web")
PACKS_DIR = Path(os.environ.get("BIAOQINGBAO_PACKS_DIR", "packs"))
DEFAULT_QR_URL = "https://github.com/REPLACE-ME/biaoqingbao"


def _make_provider(name: str = "") -> ImageProvider:
    name = name or os.environ.get("BIAOQINGBAO_PROVIDER", "gemini")
    if name == "seedream":
        from biaoqingbao.providers.seedream import SeedreamProvider

        return SeedreamProvider()
    from biaoqingbao.providers.gemini import GeminiProvider

    return GeminiProvider()


def _rembg_available() -> bool:
    return importlib.util.find_spec("rembg") is not None


@dataclass
class Job:
    id: str
    pack: Pack
    selfie: bytes
    out_dir: Path
    full: bool
    provider_name: str = ""
    status: str = "running"
    error: str = ""
    images: list[dict] = field(default_factory=list)
    collage_url: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def memes(self):
        return self.pack.memes if self.full else self.pack.free_memes


def _sticker_stem(job: Job, index: int) -> str:
    return f"{index:02d}-{job.pack.memes[index - 1].id}"


def _write_one(job: Job, index: int, raw: bytes) -> None:
    processed = maybe_remove_background(raw, enabled=_rembg_available())
    stem = _sticker_stem(job, index)
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


def _job_json(job: Job) -> dict:
    with job.lock:
        return {
            "job_id": job.id,
            "status": job.status,
            "error": job.error,
            "pack": job.pack.name,
            "total": len(job.memes),
            "images": [dict(img) for img in job.images],
            "collage_url": job.collage_url,
        }


def create_app() -> FastAPI:
    app = FastAPI(title="biaoqingbao")
    jobs: dict[str, Job] = {}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/api/packs")
    def list_packs() -> list[dict]:
        packs = []
        for path in sorted(PACKS_DIR.glob("*.yaml")):
            pack = load_pack(path)
            packs.append(
                {
                    "id": pack.id,
                    "name": pack.name,
                    "description": pack.description,
                    "meme_count": len(pack.memes),
                    "captions": [m.caption for m in pack.memes],
                }
            )
        return packs

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
            provider_name=provider,
        )
        job.images = [
            {"index": i + 1, "id": m.id, "caption": m.caption, "status": "pending", "url": "", "gif_url": ""}
            for i, m in enumerate(job.memes)
        ]
        jobs[job_id] = job
        threading.Thread(
            target=_run_generation, args=(job, _make_provider(provider)), daemon=True
        ).start()
        return {"job_id": job_id}

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
        if job.status == "running":
            raise HTTPException(409, "job still running")
        if not 1 <= index <= len(job.memes):
            raise HTTPException(400, f"index must be 1..{len(job.memes)}")
        with job.lock:
            job.status = "running"
            job.images[index - 1]["status"] = "running"
        threading.Thread(
            target=_run_retry,
            args=(job, _make_provider(job.provider_name), index, caption or None),
            daemon=True,
        ).start()
        return {"job_id": job_id}

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
<title>表情包工厂 · biaoqingbao</title>
<style>
  * { box-sizing: border-box; font-family: -apple-system, "PingFang SC", sans-serif; }
  body { margin: 0; background: #faf8f5; color: #222; }
  .wrap { max-width: 560px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 26px; margin: 8px 0 2px; }
  .sub { color: #888; font-size: 13px; margin-bottom: 20px; }
  .card { background: #fff; border: 1px solid #e8e4de; border-radius: 14px; padding: 16px; margin-bottom: 16px; }
  .label { font-size: 13px; color: #999; margin-bottom: 8px; }
  .drop { border: 2px dashed #ccc; border-radius: 12px; padding: 22px; text-align: center; cursor: pointer; color: #666; }
  .drop.has { border-color: #2a9d5c; color: #2a9d5c; }
  .drop img { max-height: 96px; border-radius: 8px; display: block; margin: 0 auto 8px; }
  .privacy { font-size: 12px; color: #aaa; text-align: center; margin-top: 8px; }
  .packs { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
  .pack { min-width: 120px; border: 2px solid #e0dcd5; border-radius: 12px; padding: 10px; cursor: pointer; font-size: 14px; }
  .pack.sel { border-color: #222; font-weight: 600; }
  .pack small { display: block; color: #999; font-size: 11px; margin-top: 4px; }
  .row { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
  button.go { flex: 1; background: #222; color: #fff; border: 0; border-radius: 999px; padding: 14px; font-size: 16px; cursor: pointer; }
  button.go:disabled { background: #bbb; cursor: not-allowed; }
  .toggle { font-size: 13px; color: #555; white-space: nowrap; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .cell { aspect-ratio: 1; border: 1px solid #e8e4de; border-radius: 10px; position: relative;
          display: flex; align-items: center; justify-content: center; overflow: hidden; background: #f3f1ed; }
  .cell img { width: 100%; height: 100%; object-fit: contain; animation: pop .3s ease; }
  @keyframes pop { from { transform: scale(.6); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  .cell .cap { position: absolute; bottom: 4px; left: 0; right: 0; text-align: center; font-size: 11px; color: #999; pointer-events: none; }
  .cell .spin { width: 22px; height: 22px; border: 3px solid #ddd; border-top-color: #555; border-radius: 50%; animation: r 1s linear infinite; }
  @keyframes r { to { transform: rotate(360deg); } }
  .cell .redo { position: absolute; top: 4px; right: 4px; font-size: 11px; background: #fff; border: 1px solid #ddd;
                border-radius: 6px; padding: 2px 6px; cursor: pointer; display: none; }
  .cell:hover .redo { display: block; }
  .collage img { width: 100%; border-radius: 12px; border: 1px solid #e8e4de; }
  .hint { font-size: 13px; color: #888; line-height: 1.7; }
  .err { color: #c0392b; font-size: 13px; white-space: pre-wrap; }
</style>
</head>
<body>
<div class="wrap">
  <h1>表情包工厂</h1>
  <div class="sub">一张自拍 → 一套微信表情包（本地运行，照片只发往你配置的 API）</div>

  <div class="card">
    <div class="label">1 · 上传一张正脸自拍</div>
    <div class="drop" id="drop" onclick="document.getElementById('file').click()">点击选择照片</div>
    <input type="file" id="file" accept="image/*" hidden>
    <div class="privacy">照片只存在内存里，服务停了就没了</div>
  </div>

  <div class="card">
    <div class="label">2 · 选梗剧本</div>
    <div class="packs" id="packs"></div>
  </div>

  <div class="card">
    <div class="row" style="margin-bottom:10px">
      <label class="toggle">模型：
        <select id="prov">
          <option value="gemini">Gemini（中转）</option>
          <option value="seedream">即梦 Seedream</option>
        </select>
      </label>
      <label class="toggle"><input type="checkbox" id="full"> 全套16张</label>
    </div>
    <div class="row">
      <button class="go" id="go" disabled>生成我的表情包</button>
    </div>
    <div class="err" id="err"></div>
  </div>

  <div class="card" id="resultCard" style="display:none">
    <div class="label">3 · 逐张揭晓（悬停可单张重摇）</div>
    <div class="grid" id="grid"></div>
  </div>

  <div class="card collage" id="collageCard" style="display:none">
    <div class="label">4 · 合集晒图卡（发朋友圈用这张）</div>
    <img id="collageImg">
    <div class="hint">单张表情：长按/右键保存 PNG，或点格子打开 GIF 版。逐张发到微信里长按「添加到表情」。</div>
  </div>
</div>

<script>
let selfie = null, packId = null, jobId = null, timer = null;

const $ = (id) => document.getElementById(id);

fetch('/api/packs').then(r => r.json()).then(packs => {
  const box = $('packs');
  packs.forEach((p, i) => {
    const div = document.createElement('div');
    div.className = 'pack' + (i === 0 ? ' sel' : '');
    div.innerHTML = `${p.name}<small>${p.meme_count} 个梗</small>`;
    div.onclick = () => { box.querySelectorAll('.pack').forEach(e => e.classList.remove('sel')); div.classList.add('sel'); packId = p.id; };
    box.appendChild(div);
    if (i === 0) packId = p.id;
  });
});

$('file').onchange = (e) => {
  selfie = e.target.files[0];
  if (!selfie) return;
  const drop = $('drop');
  drop.classList.add('has');
  drop.innerHTML = `<img src="${URL.createObjectURL(selfie)}">${selfie.name}`;
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
  timer = setInterval(poll, 800);
};

async function poll() {
  const resp = await fetch(`/api/jobs/${jobId}`);
  if (!resp.ok) return;
  const job = await resp.json();
  render(job);
  if (job.status !== 'running') {
    clearInterval(timer);
    $('go').disabled = false;
    if (job.error) $('err').textContent = job.error;
    if (job.collage_url) { $('collageImg').src = job.collage_url + '?t=' + Date.now(); $('collageCard').style.display = 'block'; }
  }
}

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
    if (img.status === 'done' && !cell.querySelector('img')) {
      cell.innerHTML = `<img src="${img.url}?t=${Date.now()}"><div class="redo">重摇</div>`;
      cell.querySelector('img').onclick = () => window.open(img.gif_url);
      cell.querySelector('.redo').onclick = (e) => { e.stopPropagation(); retry(img.index); };
    } else if (img.status === 'running' && !cell.querySelector('.spin')) {
      cell.innerHTML = `<div class="spin"></div><div class="cap">${img.caption}</div>`;
    } else if (img.status === 'pending' && !cell.querySelector('.cap')) {
      cell.innerHTML = `<div class="cap">${img.caption}</div>`;
    } else if (img.status === 'error') {
      cell.innerHTML = `<div class="cap">失败，可重摇</div><div class="redo">重摇</div>`;
      cell.querySelector('.redo').onclick = () => retry(img.index);
    }
  });
}

async function retry(index) {
  const text = prompt('想换的文案？留空保持原文案（也可以只重摇不改字）', '');
  if (text === null) return;
  const cell = $('cell-' + index);
  cell.innerHTML = '<div class="spin"></div>';
  const fd = new FormData();
  if (text.trim()) fd.append('caption', text.trim());
  await fetch(`/api/jobs/${jobId}/retry/${index}`, { method: 'POST', body: fd });
  timer = setInterval(poll, 800);
}
</script>
</body>
</html>
"""
