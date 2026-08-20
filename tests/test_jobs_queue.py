import asyncio

import pytest

from src.jobs import Clip, GenerationError, JobQueue, JobStore, QueueFullError


def make_queue(tmp_path, runner, **kw):
    store = JobStore(str(tmp_path / "jobs.db"))
    defaults = dict(max_size=10, default_timeout=5,
                    generated_dir=str(tmp_path / "generated"), retention_days=14)
    defaults.update(kw)
    return store, JobQueue(store, runner, **defaults)


async def run_until_finished(store, queue, job_id, seconds=3.0):
    worker = asyncio.create_task(queue.worker_loop())
    try:
        deadline = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < deadline:
            job = store.get(job_id)
            if job.status in ("done", "error"):
                return job
            await asyncio.sleep(0.05)
        raise AssertionError("job 沒有在期限內結束")
    finally:
        worker.cancel()


async def test_success_path(tmp_path):
    async def runner(job):
        return [Clip(id="c1", status="complete", downloadable=True, filename="c1.mp3")]

    store, queue = make_queue(tmp_path, runner)
    job = queue.submit({"mode": "simple", "prompt": "x"})
    done = await run_until_finished(store, queue, job.id)
    assert done.status == "done"
    assert done.clips[0].filename == "c1.mp3"
    assert done.started_at and done.finished_at


async def test_no_downloadable_clip_is_download_failed(tmp_path):
    async def runner(job):
        return [Clip(id="c1", status="complete", downloadable=False)]

    store, queue = make_queue(tmp_path, runner)
    job = queue.submit({"mode": "simple"})
    done = await run_until_finished(store, queue, job.id)
    assert done.status == "error"
    assert done.error == "download_failed"
    assert done.clips[0].id == "c1"  # metadata 仍要留著


async def test_generation_error_code_passthrough(tmp_path):
    async def runner(job):
        raise GenerationError("not_logged_in", "請先 login")

    store, queue = make_queue(tmp_path, runner)
    job = queue.submit({})
    done = await run_until_finished(store, queue, job.id)
    assert done.status == "error"
    assert done.error == "not_logged_in"
    assert done.error_message == "請先 login"


async def test_timeout(tmp_path):
    async def runner(job):
        await asyncio.sleep(60)

    store, queue = make_queue(tmp_path, runner)
    job = queue.submit({"timeout": 1})
    done = await run_until_finished(store, queue, job.id, seconds=4.0)
    assert done.error == "generation_timeout"


async def test_unexpected_exception_is_browser_error(tmp_path):
    async def runner(job):
        raise RuntimeError("boom")

    store, queue = make_queue(tmp_path, runner)
    job = queue.submit({})
    done = await run_until_finished(store, queue, job.id)
    assert done.error == "browser_error"
    assert "boom" in done.error_message


async def test_queue_full(tmp_path):
    async def runner(job):
        await asyncio.sleep(60)

    store, queue = make_queue(tmp_path, runner, max_size=1)
    queue.submit({})
    with pytest.raises(QueueFullError):
        queue.submit({})


def test_cleanup_expired(tmp_path):
    import os
    import time as _t
    from src.jobs import cleanup_expired

    root = tmp_path / "generated"
    old = root / "oldjob"
    new = root / "newjob"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    stale = _t.time() - 15 * 86400
    os.utime(old, (stale, stale))
    cleanup_expired(str(root), 14)
    assert not old.exists()
    assert new.exists()
