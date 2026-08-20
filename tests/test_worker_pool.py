"""多帳號：輪流派工、同帳號序列、彼此不串音。"""
import asyncio

import pytest

from src.jobs import Clip, JobQueue, JobStore
from src.suno import RawClip, SunoRunner


def make(tmp_path, runners, max_size=8):
    store = JobStore(str(tmp_path / "jobs.db"))
    return store, JobQueue(store, runners, max_size=max_size, default_timeout=5,
                           generated_dir=str(tmp_path / "g"), retention_days=14)


def _ok(worker_id, seen):
    async def runner(job):
        seen.append(worker_id)
        return [Clip(id=f"c{worker_id}", status="complete",
                     downloadable=True, filename=f"c{worker_id}.mp3")]
    return runner


def test_rotation_spreads_across_accounts(tmp_path):
    """派工要輪流,不能永遠灌給第一個帳號——多帳號就是為了攤平配額。"""
    seen = []
    _store, queue = make(tmp_path, [_ok(i, seen) for i in range(4)])
    picked = [queue.submit({"n": i}).params["worker"] for i in range(8)]
    assert picked == [0, 1, 2, 3, 0, 1, 2, 3]


async def test_busy_worker_is_skipped(tmp_path):
    """輪到的那個正在忙就順延給閒著的,不會讓別人乾等。"""
    started = asyncio.Event()

    async def slow(job):
        started.set()
        await asyncio.sleep(30)

    seen = []
    _store, queue = make(tmp_path, [slow, _ok(1, seen)])
    first = queue.submit({"n": 0})
    assert first.params["worker"] == 0

    loop0 = asyncio.create_task(queue.worker_loop(0))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        # worker 0 忙著,下一單不該再排給它
        assert queue.submit({"n": 1}).params["worker"] == 1
    finally:
        loop0.cancel()


async def test_same_account_runs_one_at_a_time(tmp_path):
    """同一個帳號不能同時跑兩單:一個 worker 只有一個瀏覽器分頁。"""
    concurrent = 0
    peak = 0

    async def counting(job):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.15)
        concurrent -= 1
        return [Clip(id="c", status="complete", downloadable=True,
                     filename="c.mp3")]

    store, queue = make(tmp_path, [counting])   # 只有一個 worker
    ids = [queue.submit({"n": i}).id for i in range(3)]
    loop = asyncio.create_task(queue.worker_loop(0))
    try:
        for _ in range(60):
            await asyncio.sleep(0.05)
            if all(store.get(i).status in ("done", "error") for i in ids):
                break
    finally:
        loop.cancel()
    assert peak == 1


async def test_two_runners_do_not_share_clip_state(tmp_path):
    """兩個帳號的 runner 各吃各的 feed,不會互相拿到對方的歌。

    這條是刻意釘住的:哪天有人把 runner 改成共用,測試會當場擋下來。
    """
    class FakeBrowser:
        page = None
        context = None

    class FakeSettings:
        generated_dir = str(tmp_path / "g")
        suno_url = "https://suno.com/create"

    a = SunoRunner(FakeBrowser(), FakeSettings())
    b = SunoRunner(FakeBrowser(), FakeSettings())

    a._clips["song-of-account-a"] = RawClip(id="song-of-account-a",
                                            status="complete")
    assert "song-of-account-a" not in b._clips
    assert b._clips == {}

    b._clips["song-of-account-b"] = RawClip(id="song-of-account-b",
                                            status="complete")
    assert set(a._clips) == {"song-of-account-a"}
    assert set(b._clips) == {"song-of-account-b"}


def test_queue_full_across_all_workers(tmp_path):
    from src.jobs import QueueFullError

    async def slow(job):
        await asyncio.sleep(30)

    _store, queue = make(tmp_path, [slow, slow], max_size=2)  # 每條 1 個位子
    queue.submit({})
    queue.submit({})
    with pytest.raises(QueueFullError):
        queue.submit({})
