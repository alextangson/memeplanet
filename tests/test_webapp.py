import io
import time

import pytest
from PIL import Image

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import biaoqingbao.webapp as webapp  # noqa: E402


class FakeProvider:
    def generate(self, prompt: str, reference: bytes) -> bytes:
        img = Image.new("RGBA", (300, 300), (255, 100, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "_make_provider", lambda: FakeProvider())
    monkeypatch.setattr(webapp, "OUTPUT_ROOT", tmp_path)
    return TestClient(webapp.create_app())


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
