import io
import time

import pytest
from PIL import Image

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import mememe.webapp as webapp  # noqa: E402


class FakeProvider:
    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt: str, reference: bytes) -> bytes:
        self.prompts.append(prompt)
        img = Image.new("RGBA", (300, 300), (255, 100, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    provider = FakeProvider()
    provider_calls: list[str] = []

    def factory(name: str = ""):
        provider_calls.append(name)
        return provider

    monkeypatch.setattr(webapp, "_make_provider", factory)
    monkeypatch.setattr(webapp, "OUTPUT_ROOT", tmp_path)
    c = TestClient(webapp.create_app())
    c.fake_provider = provider
    c.provider_calls = provider_calls
    return c


def _selfie_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), (180, 140, 110)).save(buf, format="JPEG")
    return buf.getvalue()


def _wait_done(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_index_serves_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "表情包" in resp.text


def test_list_packs(client):
    resp = client.get("/api/packs")
    assert resp.status_code == 200
    packs = resp.json()
    assert any(p["id"] == "shechu" and p["meme_count"] == 16 for p in packs)


def test_generate_job_lifecycle_with_per_image_status(client):
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    job = _wait_done(client, job_id)
    assert job["status"] == "done"
    assert len(job["images"]) == 8
    assert all(img["status"] == "done" for img in job["images"])
    assert job["images"][0]["caption"] == "收到"

    sticker = client.get(job["images"][0]["url"])
    assert sticker.status_code == 200
    assert client.get(job["collage_url"]).status_code == 200


def test_retry_single_image(client):
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)

    resp = client.post(f"/api/jobs/{job_id}/retry/4")
    assert resp.status_code == 200
    job = _wait_done(client, job_id)
    assert job["images"][3]["status"] == "done"


def test_unknown_job_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_generate_passes_provider_choice(client):
    client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu", "provider": "seedream"},
    )
    assert client.provider_calls[-1] == "seedream"


def test_retry_with_custom_caption(client):
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)

    resp = client.post(f"/api/jobs/{job_id}/retry/1", data={"caption": "老板再见"})
    assert resp.status_code == 200
    _wait_done(client, job_id)
    assert any("老板再见" in p for p in client.fake_provider.prompts)


def _tiny_gif() -> bytes:
    buf = io.BytesIO()
    img = Image.new("P", (240, 240), 0)
    img.save(buf, format="GIF")
    return buf.getvalue()


def test_generation_persists_raw_images(client, tmp_path):
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)
    raws = list(tmp_path.glob(f"{job_id}/raw-*.png"))
    assert len(raws) == 8


def test_animate_single_sticker(client, monkeypatch):
    class FakeVideo:
        def animate(self, prompt: str, image: bytes, **kw) -> bytes:
            return b"MP4BYTES"

    monkeypatch.setattr(webapp, "_make_video_provider", lambda: FakeVideo())
    monkeypatch.setattr(webapp, "mp4_to_wechat_gif", lambda mp4, **kw: _tiny_gif())

    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)

    resp = client.post(f"/api/jobs/{job_id}/animate/2")
    assert resp.status_code == 200

    import time as _t

    deadline = _t.time() + 10
    while _t.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["images"][1]["anim_status"] in ("done", "error"):
            break
        _t.sleep(0.05)
    assert job["images"][1]["anim_status"] == "done"
    assert client.get(job["images"][1]["anim_url"]).status_code == 200


def _animated_job(client) -> str:
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)
    return job_id


def _wait_anim(client, job_id, pos, timeout=10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["images"][pos]["anim_status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("anim did not finish")


def test_animate_mode_shake_needs_no_video_provider(client):
    job_id = _animated_job(client)
    resp = client.post(f"/api/jobs/{job_id}/animate/1", data={"mode": "shake"})
    assert resp.status_code == 200
    job = _wait_anim(client, job_id, 0)
    assert job["images"][0]["anim_status"] == "done"
    gif = client.get(job["images"][0]["anim_url"])
    assert gif.status_code == 200


def test_animate_mode_frames_uses_image_provider(client):
    job_id = _animated_job(client)
    before = len(client.fake_provider.prompts)
    resp = client.post(f"/api/jobs/{job_id}/animate/2", data={"mode": "frames"})
    assert resp.status_code == 200
    job = _wait_anim(client, job_id, 1)
    assert job["images"][1]["anim_status"] == "done"
    assert len(client.fake_provider.prompts) == before + 1  # one keyframe edit call


def test_job_meta_persisted_to_disk(client, tmp_path):
    job_id = _animated_job(client)
    import json

    meta = json.loads((tmp_path / job_id / "job.json").read_text())
    assert meta["pack_name"] == "社畜的一天"
    assert len(meta["images"]) == 8
    assert "selfie" not in meta  # privacy: never persisted


def test_history_requires_explicit_ids(client):
    job_id = _animated_job(client)
    # 不带 ids 不再泄漏全量任务——历史归属随浏览器 localStorage
    assert client.get("/api/history").json() == []
    items = client.get("/api/history", params={"ids": f"{job_id},nonexistent"}).json()
    assert [i["job_id"] for i in items] == [job_id]
    assert items[0]["pack_name"]


def test_html_pages_send_no_cache_header(client):
    # 没有这个头浏览器会启发式缓存，用户改版后看到的还是旧页面
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/custom").headers["cache-control"] == "no-cache"


def test_platform_pack_includes_anim_gifs(client):
    import zipfile as _zipfile

    job_id = _animated_job(client)
    client.post(f"/api/jobs/{job_id}/animate/1", data={"mode": "shake"})
    _wait_anim(client, job_id, 0)

    resp = client.get(f"/api/jobs/{job_id}/platform-pack")
    assert resp.status_code == 200
    names = set(_zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
    assert "主图/01.png" in names
    assert "动图/01.gif" in names  # 转过动图的表情要进素材包
    assert "动图/02.gif" not in names


def test_default_provider_is_gemini_relay():
    # 中转站包月 → 主力；即梦按量计费只做兜底（用户成本决策）
    assert webapp.DEFAULT_PROVIDER == "gemini"


def test_generation_falls_back_per_image_on_primary_failure(client, tmp_path, monkeypatch):
    good = FakeProvider()

    class FlakyPrimary:
        def __init__(self):
            self.n = 0

        def generate(self, prompt, reference):
            self.n += 1
            if self.n in (2, 5):  # 第 2、5 张主力翻车
                raise RuntimeError("seedream 500")
            return good.generate(prompt, reference)

    primary = FlakyPrimary()

    def factory(name=""):
        return good if name == "gemini" else primary

    monkeypatch.setattr(webapp, "_make_provider", factory)
    monkeypatch.setattr(webapp, "_fallback_image_provider", lambda n: good)

    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job = _wait_done(client, resp.json()["job_id"])
    assert job["status"] == "done"
    assert all(img["status"] == "done" for img in job["images"])  # 失败的两张靠 fallback 补上


def test_admin_disabled_without_key(client, monkeypatch):
    monkeypatch.setattr(webapp, "ADMIN_KEY", "")
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/data", params={"key": "whatever"}).status_code == 404


def test_admin_requires_correct_key(client, monkeypatch):
    monkeypatch.setattr(webapp, "ADMIN_KEY", "s3cret")
    assert client.get("/api/admin/data", params={"key": "wrong"}).status_code == 403
    assert client.get("/api/admin/data").status_code == 403
    assert client.get("/admin").status_code == 200  # 页面本身无秘密，凭 key 拉数据
    ok = client.get("/api/admin/data", params={"key": "s3cret"})
    assert ok.status_code == 200
    body = ok.json()
    assert "leads" in body and "funnel" in body and "errors" in body and "jobs" in body


def test_admin_data_aggregates_leads_events_jobs(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "ADMIN_KEY", "k")
    monkeypatch.setattr(webapp, "LEADS_FILE", tmp_path / "leads.jsonl")
    monkeypatch.setattr(webapp, "EVENTS_FILE", tmp_path / "events.jsonl")

    client.post("/api/leads", data={"contact": "wx123", "need": "咖啡店吉祥物"})
    client.post("/api/events", data={"name": "unlock_shown"})
    client.post("/api/events", data={"name": "unlock_shown"})
    client.post("/api/events", data={"name": "unlock_free_click"})
    client.post("/api/events", data={"name": "js_error", "detail": "boom @ x:1"})
    job_id = _animated_job(client)  # 一个完成的任务进 jobs 统计

    data = client.get("/api/admin/data", params={"key": "k"}).json()
    assert any(l["contact"] == "wx123" for l in data["leads"])
    assert data["funnel"]["unlock_shown"] == 2
    assert data["funnel"]["unlock_free_click"] == 1
    assert any("boom" in e["detail"] for e in data["errors"])
    assert data["jobs"]["total"] >= 1
    mine = next(j for j in data["jobs"]["recent"] if j["job_id"] == job_id)
    assert mine["thumb"]  # admin 保留全量并带缩略图，排查生成质量用


def test_generate_rejects_non_image(client):
    resp = client.post(
        "/api/generate",
        files={"selfie": ("evil.txt", b"not an image", "text/plain")},
        data={"pack_id": "shechu"},
    )
    assert resp.status_code == 400
    assert "图片" in resp.json()["detail"]


def test_generate_rejects_oversized_upload(client):
    big = b"\xff\xd8\xff" + b"0" * (webapp.MAX_UPLOAD_BYTES + 1)
    resp = client.post(
        "/api/generate",
        files={"selfie": ("big.jpg", big, "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    assert resp.status_code == 413


def test_generate_returns_429_when_at_capacity(client, monkeypatch):
    import threading as _t

    # 占满并发名额 → 下一个请求应被婉拒而不是把机器压垮
    monkeypatch.setattr(webapp, "_GEN_SLOTS", _t.Semaphore(0))
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    assert resp.status_code == 429


def test_logo_served(client):
    resp = client.get("/logo.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_contact_qr_served_only_when_configured(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "CONTACT_QR_FILE", tmp_path / "contact-qr.png")
    assert client.get("/api/contact-qr").status_code == 404

    buf = io.BytesIO()
    Image.new("RGB", (100, 100), (0, 0, 0)).save(buf, format="PNG")
    (tmp_path / "contact-qr.png").write_bytes(buf.getvalue())
    resp = client.get("/api/contact-qr")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_qr_url_overridable_via_env(monkeypatch):
    import importlib

    monkeypatch.setenv("MEMEME_QR_URL", "https://meme-planet.com/")
    importlib.reload(webapp)
    try:
        assert webapp.DEFAULT_QR_URL == "https://meme-planet.com/"
    finally:
        monkeypatch.delenv("MEMEME_QR_URL")
        importlib.reload(webapp)


def test_events_endpoint_appends_jsonl(client, tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setattr(webapp, "EVENTS_FILE", tmp_path / "events.jsonl")
    r = client.post("/api/events", data={"name": "unlock_shown", "job_id": "abc"})
    assert r.status_code == 200
    r = client.post("/api/events", data={"name": "unlock_free_click"})
    assert r.status_code == 200

    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = _json.loads(lines[0])
    assert first["name"] == "unlock_shown"
    assert first["job_id"] == "abc"
    assert first["ts"] > 0

    assert client.post("/api/events", data={"name": "bad name!"}).status_code == 422


def test_failed_job_with_no_images_hidden_from_history(client, monkeypatch):
    class FailingProvider:
        def generate(self, prompt: str, reference: bytes) -> bytes:
            raise RuntimeError("provider 500")

    monkeypatch.setattr(webapp, "_make_provider", lambda name="": FailingProvider())
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    job = _wait_done(client, job_id)
    assert job["status"] == "error"

    items = client.get("/api/history", params={"ids": job_id}).json()
    assert all(i["job_id"] != job_id for i in items)  # 0 张完成 → 即使自己的也不进历史


def test_jobs_survive_restart_but_retry_is_blocked(client, tmp_path, monkeypatch):
    job_id = _animated_job(client)

    fresh = TestClient(webapp.create_app())  # simulates restart, same OUTPUT_ROOT
    job = fresh.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert all(i["status"] == "done" for i in job.json()["images"])
    assert fresh.get(job.json()["images"][0]["url"]).status_code == 200

    resp = fresh.post(f"/api/jobs/{job_id}/retry/1")
    assert resp.status_code == 409  # selfie gone by privacy design


def test_frames_falls_back_to_other_provider(client, tmp_path, monkeypatch):
    good = FakeProvider()

    class FailingProvider:
        def generate(self, prompt: str, reference: bytes) -> bytes:
            raise RuntimeError("relay 500")

    # 默认主力 gemini 失败 → 回退 seedream（good）补上
    def factory(name: str = ""):
        return good if name == "seedream" else FailingProvider()

    job_id = _animated_job(client)  # generated with the original fixture provider
    monkeypatch.setattr(webapp, "_make_provider", factory)

    resp = client.post(f"/api/jobs/{job_id}/animate/1", data={"mode": "frames"})
    assert resp.status_code == 200
    job = _wait_anim(client, job_id, 0)
    assert job["images"][0]["anim_status"] == "done"
    assert len(good.prompts) == 1  # fallback provider did the edit


def test_frames_on_job_without_raw_rejected(client, tmp_path):
    job_id = _animated_job(client)
    for raw in (tmp_path / job_id).glob("raw-*.png"):
        raw.unlink()  # simulate pre-persistence history job
    resp = client.post(f"/api/jobs/{job_id}/animate/1", data={"mode": "frames"})
    assert resp.status_code == 409
    assert "抖一抖" in resp.json()["detail"]


def test_packs_expose_preview_url(client):
    packs = client.get("/api/packs").json()
    shechu = next(p for p in packs if p["id"] == "shechu")
    assert shechu["preview_url"] == "/api/pack-preview/shechu"
    resp = client.get(shechu["preview_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_unknown_pack_preview_404(client):
    assert client.get("/api/pack-preview/nope").status_code == 404


@pytest.fixture()
def agent_client(client, tmp_path, monkeypatch):
    import json as _json

    from mememe.core.scriptwriter import Scriptwriter

    draft = {
        "id": "dingzhi",
        "name": "定制包",
        "description": "测试定制",
        "subject": "person",
        "vibe": "测试氛围",
        "memes": [
            {"id": f"m{i}", "caption": f"梗{i}", "expression": "表情",
             "action": "动作", "shot": "半身"}
            for i in range(16)
        ],
    }

    def fake_chat(messages, *, json_mode=False):
        if json_mode:
            return _json.dumps(draft, ensure_ascii=False)
        return "想给谁做表情包呀？"

    monkeypatch.setattr(webapp, "_make_scriptwriter", lambda: Scriptwriter(fake_chat))
    monkeypatch.setattr(webapp, "CUSTOM_PACKS_DIR", tmp_path / "custom")
    return client


def test_agent_chat_keeps_history(agent_client):
    r1 = agent_client.post("/api/agent/chat", data={"message": "我想定制"})
    assert r1.status_code == 200
    draft_id = r1.json()["draft_id"]
    assert "表情包" in r1.json()["reply"]

    r2 = agent_client.post(
        "/api/agent/chat", data={"message": "给对象用", "draft_id": draft_id}
    )
    assert r2.json()["draft_id"] == draft_id


def test_agent_draft_creates_custom_pack_private_to_creator(agent_client):
    draft_id = agent_client.post(
        "/api/agent/chat", data={"message": "程序员上线日"}
    ).json()["draft_id"]

    resp = agent_client.post("/api/agent/draft", data={"draft_id": draft_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pack_id"] == "dingzhi"

    # 定制剧本不进公共列表——只有持有链接的创建者可见
    packs = agent_client.get("/api/packs").json()
    assert all(p["id"] != "dingzhi" for p in packs)

    single = agent_client.get("/api/packs/dingzhi")
    assert single.status_code == 200
    assert single.json()["custom"] is True
    assert single.json()["meme_count"] == 16


def test_get_pack_by_id(client):
    resp = client.get("/api/packs/shechu")
    assert resp.status_code == 200
    assert resp.json()["custom"] is False
    assert client.get("/api/packs/nope").status_code == 404


def test_generate_works_with_custom_pack(agent_client):
    draft_id = agent_client.post(
        "/api/agent/chat", data={"message": "随便"}
    ).json()["draft_id"]
    agent_client.post("/api/agent/draft", data={"draft_id": draft_id})

    resp = agent_client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "dingzhi"},
    )
    assert resp.status_code == 200
    job = _wait_done(agent_client, resp.json()["job_id"])
    assert job["status"] == "done"


def test_animate_with_custom_motion(client, monkeypatch):
    prompts = []

    class FakeVideo:
        def animate(self, prompt: str, image: bytes, **kw) -> bytes:
            prompts.append(prompt)
            return b"MP4"

    monkeypatch.setattr(webapp, "_make_video_provider", lambda: FakeVideo())
    monkeypatch.setattr(webapp, "mp4_to_wechat_gif", lambda mp4, **kw: _tiny_gif())

    job_id = _animated_job(client)
    resp = client.post(
        f"/api/jobs/{job_id}/animate/1",
        data={"mode": "video", "motion": "举着咖啡杯慢动作干杯"},
    )
    assert resp.status_code == 200
    _wait_anim(client, job_id, 0)
    assert "举着咖啡杯慢动作干杯" in prompts[0]


def test_custom_pack_preview_generated_and_served(agent_client, tmp_path, monkeypatch):
    import io as _io

    from PIL import Image as _Image

    def fake_t2i(prompt: str) -> bytes:
        buf = _io.BytesIO()
        _Image.new("RGBA", (300, 300), (10, 180, 90, 255)).save(buf, format="PNG")
        return buf.getvalue()

    monkeypatch.setattr(webapp, "_make_t2i", lambda: fake_t2i)
    draft_id = agent_client.post(
        "/api/agent/chat", data={"message": "树懒"}
    ).json()["draft_id"]
    resp = agent_client.post("/api/agent/draft", data={"draft_id": draft_id})
    pack_id = resp.json()["pack_id"]

    import time as _t

    deadline = _t.time() + 5
    preview = webapp.CUSTOM_PACKS_DIR / "previews" / f"{pack_id}.png"
    while _t.time() < deadline and not preview.exists():
        _t.sleep(0.05)
    assert preview.exists()
    pack = agent_client.get(f"/api/packs/{pack_id}").json()
    assert pack["preview_url"] == f"/api/pack-preview/{pack_id}"
    assert agent_client.get(pack["preview_url"]).status_code == 200


def test_generate_snapshots_pack_into_job_dir(agent_client, tmp_path):
    # 任务自带剧本快照 → 即使源 yaml 日后丢失，动图/重摇/续生成仍可用
    draft_id = agent_client.post(
        "/api/agent/chat", data={"message": "随便"}
    ).json()["draft_id"]
    agent_client.post("/api/agent/draft", data={"draft_id": draft_id})
    resp = agent_client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "dingzhi"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(agent_client, job_id)

    from mememe.core.schema import load_pack

    snapshot = tmp_path / job_id / "pack.yaml"
    assert snapshot.exists()
    assert load_pack(snapshot).id == "dingzhi"


def test_custom_pack_job_animates_after_pack_file_lost(agent_client, tmp_path):
    # 复现生产事故：用户的私有定制剧本从 packs/custom 消失后，重启仍要能转动图
    draft_id = agent_client.post(
        "/api/agent/chat", data={"message": "随便"}
    ).json()["draft_id"]
    agent_client.post("/api/agent/draft", data={"draft_id": draft_id})
    resp = agent_client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "dingzhi"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(agent_client, job_id)

    (webapp.CUSTOM_PACKS_DIR / "dingzhi.yaml").unlink()  # 剧本文件丢失

    fresh = TestClient(webapp.create_app())  # 重启：从磁盘重建任务
    job = fresh.get(f"/api/jobs/{job_id}").json()
    assert job["pack_name"]

    resp = fresh.post(f"/api/jobs/{job_id}/animate/1", data={"mode": "frames"})
    assert resp.status_code == 200  # 旧实现：409「找不到该任务的剧本文件」


def test_load_jobs_clears_stuck_anim_status(client, tmp_path):
    # 重启时正在转的动图卡在 running → 不能永远显示「动图中」
    import json as _json

    job_id = _animated_job(client)
    p = tmp_path / job_id / "job.json"
    meta = _json.loads(p.read_text())
    meta["images"][0]["anim_status"] = "running"
    p.write_text(_json.dumps(meta, ensure_ascii=False))

    fresh = TestClient(webapp.create_app())
    job = fresh.get(f"/api/jobs/{job_id}").json()
    assert job["images"][0]["anim_status"] == "error"


def test_load_jobs_persists_reset_to_disk(client, tmp_path):
    # 重置必须回写磁盘——否则巡检/admin 读盘永远误报僵尸 running
    import json as _json

    job_id = _animated_job(client)
    p = tmp_path / job_id / "job.json"
    meta = _json.loads(p.read_text())
    meta["status"] = "running"
    meta["images"][0]["anim_status"] = "running"
    p.write_text(_json.dumps(meta, ensure_ascii=False))

    TestClient(webapp.create_app())  # 重启加载即回写

    on_disk = _json.loads(p.read_text())
    assert on_disk["status"] == "error"
    assert on_disk["images"][0]["anim_status"] == "error"


def test_load_jobs_does_not_rewrite_healthy_jobs(client, tmp_path):
    # 健康任务不该被无谓回写（避免每次重启刷一遍 mtime）
    import json as _json

    job_id = _animated_job(client)
    p = tmp_path / job_id / "job.json"
    before = p.stat().st_mtime_ns

    TestClient(webapp.create_app())
    assert p.stat().st_mtime_ns == before


def test_styles_endpoint(client):
    data = client.get("/api/styles").json()
    assert any(s["id"] == "bojack" for s in data["styles"])
    assert any(c["id"] == "bold" for c in data["caption_styles"])

    from mememe.core.styles import STYLES

    assert {s["id"] for s in data["styles"]} == set(STYLES)


def test_generate_carries_style_through_retry(client, tmp_path):
    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu", "style": "bojack", "caption_style": "bold"},
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)
    assert any("画风指定" in p for p in client.fake_provider.prompts)
    assert any("文字样式" in p for p in client.fake_provider.prompts)

    before = len(client.fake_provider.prompts)
    client.post(f"/api/jobs/{job_id}/retry/1")
    _wait_done(client, job_id)
    assert "画风指定" in client.fake_provider.prompts[before]  # 重摇沿用画风

    import json as _json

    meta = _json.loads((tmp_path / job_id / "job.json").read_text())
    assert meta["style"] == "bojack" and meta["caption_style"] == "bold"


def test_platform_pack_download(client):
    job_id = _animated_job(client)
    resp = client.get(f"/api/jobs/{job_id}/platform-pack")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # zip magic
    assert client.get("/api/jobs/nope/platform-pack").status_code == 404


def test_extend_generates_remaining_eight(client):
    job_id = _animated_job(client)  # free tier: 8 done
    resp = client.post(f"/api/jobs/{job_id}/extend")
    assert resp.status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert len(job["images"]) == 16
    assert all(i["status"] == "done" for i in job["images"])
    assert job["images"][8]["caption"]  # 第 9 个梗有文案

    # 已是全套则拒绝
    assert client.post(f"/api/jobs/{job_id}/extend").status_code == 409


def test_extend_blocked_without_selfie(client):
    job_id = _animated_job(client)
    fresh = TestClient(webapp.create_app())
    assert fresh.post(f"/api/jobs/{job_id}/extend").status_code == 409


def test_custom_page_served(client):
    resp = client.get("/custom")
    assert resp.status_code == 200
    assert "定制" in resp.text


def test_leads_endpoint_appends_jsonl(client, tmp_path, monkeypatch):
    lead_file = tmp_path / "leads.jsonl"
    monkeypatch.setattr(webapp, "LEADS_FILE", lead_file)
    resp = client.post(
        "/api/leads", data={"contact": "wx: hello123", "need": "品牌吉祥物一套"}
    )
    assert resp.status_code == 200
    import json as _json

    line = _json.loads(lead_file.read_text().strip())
    assert line["contact"] == "wx: hello123"


def test_styles_sorted_by_popularity(client):
    for style in ["anime", "anime", "bojack"]:
        resp = client.post(
            "/api/generate",
            files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
            data={"pack_id": "shechu", "style": style},
        )
        _wait_done(client, resp.json()["job_id"])
    data = client.get("/api/styles").json()
    ids = [s["id"] for s in data["styles"]]
    assert ids[0] == "anime"
    assert ids.index("anime") < ids.index("bojack")
    by_id = {s["id"]: s for s in data["styles"]}
    assert by_id["anime"]["uses"] == 2
    assert by_id["bojack"]["uses"] == 1
    assert by_id["felt"]["uses"] == 0


def test_backend_failure_logged_for_admin_triage(client, tmp_path, monkeypatch):
    # 后端异常必须可排查：server_error 落 events.jsonl（带 scope/job/张数），admin 能看到
    import json as _json

    monkeypatch.setattr(webapp, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(webapp, "ADMIN_KEY", "k")

    class Boom:
        def generate(self, prompt, reference):
            raise RuntimeError("relay 503")

    monkeypatch.setattr(webapp, "_make_provider", lambda name="": Boom())
    monkeypatch.setattr(webapp, "_fallback_image_provider", lambda n: None)

    resp = client.post(
        "/api/generate",
        files={"selfie": ("me.jpg", _selfie_bytes(), "image/jpeg")},
        data={"pack_id": "shechu"},
    )
    job_id = resp.json()["job_id"]
    job = _wait_done(client, job_id)
    assert job["status"] == "error"

    rows = [
        _json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    errs = [r for r in rows if r["name"] == "server_error"]
    assert errs, "后端异常没有落盘"
    assert errs[0]["scope"] == "generate"
    assert errs[0]["job_id"] == job_id
    assert "RuntimeError" in errs[0]["detail"]

    data = client.get("/api/admin/data", params={"key": "k"}).json()
    assert any("relay 503" in e["detail"] for e in data["errors"])
    # 每张图的错误各自归属，不再互相覆盖
    assert all("relay 503" in (i.get("error") or "") for i in job["images"])


def test_video_animations_queue_through_slots(client, monkeypatch):
    # 视频任务串行排队（防超方舟并发配额→排队超时），不是拒绝也不是放飞
    import threading as _t

    monkeypatch.setattr(webapp, "_VIDEO_SLOTS", _t.Semaphore(1))
    monkeypatch.setattr(webapp, "mp4_to_wechat_gif", lambda mp4, **kw: _tiny_gif())
    gate = _t.Event()
    started: list = []

    class SlowVideo:
        def animate(self, prompt, image, **kw):
            started.append(1)
            gate.wait(5)
            return b"MP4"

    monkeypatch.setattr(webapp, "_make_video_provider", lambda: SlowVideo())
    job_id = _animated_job(client)
    assert client.post(f"/api/jobs/{job_id}/animate/1", data={"mode": "video"}).status_code == 200
    assert client.post(f"/api/jobs/{job_id}/animate/2", data={"mode": "video"}).status_code == 200
    time.sleep(0.4)
    assert len(started) == 1  # 第二个在信号量上排队，没并发打上游
    gate.set()
    _wait_anim(client, job_id, 0)
    job = _wait_anim(client, job_id, 1)
    assert job["images"][0]["anim_status"] == "done"
    assert job["images"][1]["anim_status"] == "done"


def test_drafts_survive_restart(agent_client, tmp_path, monkeypatch):
    # 用户跟 AI 编剧聊到一半赶上部署重启，不能「对话不存在」
    monkeypatch.setattr(webapp, "DRAFTS_DIR", tmp_path / "drafts")
    draft_id = agent_client.post(
        "/api/agent/chat", data={"message": "程序员上线日"}
    ).json()["draft_id"]

    fresh = TestClient(webapp.create_app())  # 重启
    resp = fresh.post("/api/agent/draft", data={"draft_id": draft_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pack_id"]
