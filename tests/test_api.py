import asyncio

from fastapi.testclient import TestClient

from src.config import Settings
from src.jobs import Clip, JobQueue, JobStore
from src.main import create_app


def make_client(tmp_path, monkeypatch, *, runner=None, api_keys="", max_size=10):
    monkeypatch.setenv("API_KEYS", api_keys)
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))
    settings = Settings()

    async def default_runner(job):
        return [Clip(id="c1", status="complete", downloadable=True, filename="c1.mp3")]

    store = JobStore(str(tmp_path / "jobs.db"))
    queue = JobQueue(store, runner or default_runner,
                     max_size=max_size, default_timeout=5,
                     generated_dir=settings.generated_dir, retention_days=14)
    app = create_app(settings=settings, store=store, queue=queue,
                     health_extra=lambda: {"browser_alive": True, "logged_in": True})
    return TestClient(app), store


def poll_done(client, job_id, headers=None, tries=100):
    for _ in range(tries):
        job = client.get(f"/api/jobs/{job_id}", headers=headers or {}).json()
        if job["status"] in ("done", "error"):
            return job
    raise AssertionError("job 沒完成")


def test_submit_and_poll_done(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        resp = client.post("/api/generate", json={"prompt": "a happy tune"})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        job = poll_done(client, job_id)
        assert job["status"] == "done"
        assert job["clips"][0]["audio_url"].endswith("/files/c1.mp3")


def test_empty_request_is_400(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        assert client.post("/api/generate", json={}).status_code == 400
        assert client.post("/api/generate", json={"prompt": "  "}).status_code == 400


def test_custom_mode_params(tmp_path, monkeypatch):
    captured = {}

    async def runner(job):
        captured.update(job.params)
        return [Clip(id="c1", status="complete", downloadable=True, filename="c1.mp3")]

    client, _ = make_client(tmp_path, monkeypatch, runner=runner)
    with client:
        resp = client.post("/api/generate", json={
            "prompt": "會被忽略", "lyrics": "詞", "style": "lo-fi",
            "title": "夜", "instrumental": True})
        poll_done(client, resp.json()["job_id"])
    assert captured["mode"] == "custom"
    assert captured["instrumental"] is True
    assert captured["title"] == "夜"


def test_api_key_enforced(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch, api_keys="secret1")
    with client:
        assert client.post("/api/generate", json={"prompt": "x"}).status_code == 403
        assert client.get("/api/jobs/whatever").status_code == 403
        ok = client.post("/api/generate", json={"prompt": "x"},
                         headers={"x-api-key": "secret1"})
        assert ok.status_code == 200


def test_job_not_found_is_404(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/jobs/nonexistent00").status_code == 404


def test_queue_full_is_429(tmp_path, monkeypatch):
    async def slow_runner(job):
        await asyncio.sleep(60)

    client, _ = make_client(tmp_path, monkeypatch, runner=slow_runner, max_size=1)
    with client:
        codes = [client.post("/api/generate", json={"prompt": "x"}).status_code
                 for _ in range(4)]
    assert 429 in codes


def test_file_endpoint_serves_and_guards(tmp_path, monkeypatch):
    client, store = make_client(tmp_path, monkeypatch)
    job = store.create({})
    d = tmp_path / "generated" / job.id
    d.mkdir(parents=True)
    (d / "c1.mp3").write_bytes(b"x" * 10)
    with client:
        ok = client.get(f"/api/jobs/{job.id}/files/c1.mp3")
        assert ok.status_code == 200
        assert client.get(f"/api/jobs/{job.id}/files/..%2Fsecret").status_code == 404
        assert client.get(f"/api/jobs/{job.id}/files/none.mp3").status_code == 404


def test_health_shape(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        h = client.get("/api/health").json()
    assert h["status"] == "ok"
    assert h["browser_alive"] is True
    assert h["logged_in"] is True
    assert "queue_size" in h and "uptime_seconds" in h and "credits" in h
