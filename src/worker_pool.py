"""多帳號 worker：一個 worker 綁一個 Suno 帳號（一個 profile 目錄）。

瀏覽器是隨用隨開的：派工到某個 worker 才啟動它的 Chrome，閒置超過
`IDLE_SHUTDOWN_MINUTES` 就關掉。這服務一個月幾十單、其餘時間全在閒置，
常駐四個真 Chrome 要吃近 5 GB 記憶體；隨用隨開每單只多約 10 到 15 秒，
相對 2 到 4 分鐘的生成可以忽略。

每個 worker 有自己的 BrowserManager 與自己的 SunoRunner，所以 clip 的
側錄資料天生就是各自獨立的——A 帳號的 runner 看不到 B 帳號的 feed。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .browser import BrowserManager
from .config import Settings, get_worker_profile_dir
from .jobs import Clip, Job
from .suno import SunoRunner

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, worker_id: int, settings: Settings) -> None:
        self.id = worker_id
        self.profile_dir = get_worker_profile_dir(worker_id)
        self._settings = settings
        self._browser: BrowserManager | None = None
        self._runner: SunoRunner | None = None
        self._lock = asyncio.Lock()
        self.jobs_done = 0
        self.jobs_failed = 0
        self.last_used: float = 0.0
        self.busy = False

    async def _ensure_browser(self) -> SunoRunner:
        async with self._lock:
            if self._browser is None or not self._browser.is_alive():
                if self._browser is not None:
                    await self._browser.stop()
                log.info("worker %s：啟動瀏覽器（%s）", self.id, self.profile_dir)
                self._browser = BrowserManager(profile_dir=self.profile_dir)
                await self._browser.start()
                self._runner = SunoRunner(self._browser, self._settings)
            assert self._runner is not None
            return self._runner

    async def run(self, job: Job) -> list[Clip]:
        self.busy = True
        self.last_used = time.time()
        try:
            runner = await self._ensure_browser()
            clips = await runner.run(job)
            self.jobs_done += 1
            return clips
        except Exception:
            self.jobs_failed += 1
            raise
        finally:
            self.busy = False
            self.last_used = time.time()

    async def stop(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.stop()
                self._browser = None
                self._runner = None

    async def stop_if_idle(self, idle_seconds: float) -> bool:
        """閒置夠久就把瀏覽器關掉，回報有沒有真的關。"""
        if (self.busy or self._browser is None or idle_seconds <= 0
                or time.time() - self.last_used < idle_seconds):
            return False
        log.info("worker %s：閒置超過 %.0f 秒，關掉瀏覽器", self.id, idle_seconds)
        await self.stop()
        return True

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile": self.profile_dir,
            "browser_up": self._browser is not None and self._browser.is_alive(),
            "busy": self.busy,
            "logged_in": self._runner.logged_in if self._runner else None,
            "credits": self._runner.last_credits if self._runner else None,
            "jobs_done": self.jobs_done,
            "jobs_failed": self.jobs_failed,
            "last_used": self.last_used or None,
        }


class WorkerPool:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workers = [Worker(i, settings) for i in range(max(1, settings.worker_count))]

    @property
    def runners(self) -> list:
        """交給 JobQueue 的執行函式，一個 worker 一支。"""
        return [w.run for w in self.workers]

    def credits_of(self, index: int) -> int | None:
        """那個帳號目前觀測到的剩餘點數，給 JobQueue 挑帳號用。"""
        if 0 <= index < len(self.workers):
            r = self.workers[index]._runner
            return r.last_credits if r else None
        return None

    def infos(self) -> list[dict[str, Any]]:
        return [w.info() for w in self.workers]

    def summary(self) -> dict[str, Any]:
        """給 /api/health 用的彙總。credits 取所有觀測得到的加總。"""
        infos = self.infos()
        seen = [i["credits"] for i in infos if isinstance(i["credits"], int)]
        return {
            "browser_alive": any(i["browser_up"] for i in infos) or True,
            "logged_in": (True if any(i["logged_in"] for i in infos)
                          else (False if any(i["logged_in"] is False for i in infos)
                                else None)),
            "credits": sum(seen) if seen else None,
            "workers": infos,
        }

    async def idle_sweeper(self) -> None:
        """定期把閒著的瀏覽器關掉，省記憶體。"""
        idle_seconds = self.settings.idle_shutdown_minutes * 60
        if idle_seconds <= 0:
            return
        while True:
            await asyncio.sleep(60)
            for w in self.workers:
                try:
                    await w.stop_if_idle(idle_seconds)
                except Exception as e:  # 關瀏覽器失敗不該把 sweeper 弄死
                    log.warning("worker %s 關閉失敗：%s", w.id, e)

    async def stop_all(self) -> None:
        for w in self.workers:
            try:
                await w.stop()
            except Exception:
                pass
