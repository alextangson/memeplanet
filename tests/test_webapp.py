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


def test_history_endpoint_lists_jobs(client):
    job_id = _animated_job(client)
    resp = client.get("/api/history")
    assert resp.status_code == 200
    items = resp.json()
    assert any(i["job_id"] == job_id for i in items)
    assert items[0]["pack_name"]


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
