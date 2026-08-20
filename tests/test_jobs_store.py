from src.jobs import Clip, Job, JobStore


def test_create_and_get_roundtrip(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    job = store.create({"mode": "simple", "prompt": "hi", "timeout": 600})
    assert job.status == "queued"
    assert len(job.id) == 12

    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.params["prompt"] == "hi"
    assert loaded.clips == []


def test_save_updates_status_and_clips(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    job = store.create({"mode": "simple"})
    job.status = "done"
    job.clips = [Clip(id="fakeclip0001", title="測試曲", status="complete",
                      duration=123.4, downloadable=True, filename="fakeclip0001.mp3")]
    store.save(job)

    loaded = store.get(job.id)
    assert loaded.status == "done"
    assert loaded.clips[0].downloadable is True
    assert loaded.clips[0].filename == "fakeclip0001.mp3"


def test_get_missing_returns_none(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    assert store.get("nope") is None


def test_to_api_shapes():
    clip = Clip(id="fakeclip0001", title="t", status="complete", duration=10.0,
                downloadable=True, filename="fakeclip0001.mp3",
                image_filename="fakeclip0001.jpeg")
    locked = Clip(id="fakeclip0002", title="t2", status="complete", duration=10.0)
    job = Job(id="abcabcabcabc", status="done", clips=[clip, locked],
              created_at=1.0, started_at=2.0, finished_at=5.0)
    api = job.to_api()
    assert api["job_id"] == "abcabcabcabc"
    assert api["elapsed_seconds"] == 3.0
    assert api["clips"][0]["audio_url"] == "/api/jobs/abcabcabcabc/files/fakeclip0001.mp3"
    assert api["clips"][0]["image_url"] == "/api/jobs/abcabcabcabc/files/fakeclip0001.jpeg"
    assert api["clips"][1]["downloadable"] is False
    assert "audio_url" not in api["clips"][1]
