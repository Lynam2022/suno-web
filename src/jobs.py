"""Job 資料模型、SQLite 儲存、佇列與 worker"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class Clip:
    id: str
    title: str = ""
    status: str = ""              # Suno 端狀態：complete / error / ...
    duration: float | None = None
    downloadable: bool = False
    filename: str | None = None            # 已落地音檔檔名（generated/<job_id>/ 下）
    image_filename: str | None = None

    def to_api(self, job_id: str) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "title": self.title, "status": self.status,
            "duration": self.duration, "downloadable": self.downloadable,
        }
        if self.filename:
            d["audio_url"] = f"/api/jobs/{job_id}/files/{self.filename}"
        if self.image_filename:
            d["image_url"] = f"/api/jobs/{job_id}/files/{self.image_filename}"
        return d


@dataclass
class Job:
    id: str
    status: str = "queued"        # queued / generating / done / error
    params: dict = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)
    error: str | None = None
    error_message: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    def to_api(self) -> dict[str, Any]:
        elapsed = None
        if self.started_at:
            elapsed = round((self.finished_at or time.time()) - self.started_at, 1)
        return {
            "job_id": self.id, "status": self.status,
            "clips": [c.to_api(self.id) for c in self.clips],
            "error": self.error, "error_message": self.error_message,
            "elapsed_seconds": elapsed,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    params TEXT NOT NULL,
    clips TEXT NOT NULL,
    error TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
)
"""


class JobStore:
    """SQLite job 記錄。服務重啟後查舊 job 不會 404。"""

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def create(self, params: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], params=params, created_at=time.time())
        self.save(job)
        return job

    def save(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (job.id, job.status,
                 json.dumps(job.params, ensure_ascii=False),
                 json.dumps([c.__dict__ for c in job.clips], ensure_ascii=False),
                 job.error, job.error_message,
                 job.created_at, job.started_at, job.finished_at),
            )
            self._conn.commit()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id,status,params,clips,error,error_message,"
                "created_at,started_at,finished_at FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return Job(
            id=row[0], status=row[1], params=json.loads(row[2]),
            clips=[Clip(**c) for c in json.loads(row[3])],
            error=row[4], error_message=row[5],
            created_at=row[6], started_at=row[7], finished_at=row[8],
        )

    def fail_unfinished(self, message: str) -> int:
        """服務重啟後，把所有還沒跑到終態的 job 一律標記失敗。Queue 是純記憶體
        佇列，重啟後這些 job 永遠不會再被排到，不處理的話 client 會永遠輪詢
        不到終態。回傳受影響的筆數。"""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status='error', error='browser_error', "
                "error_message=?, finished_at=? "
                "WHERE status NOT IN ('done','error')",
                (message, now),
            )
            self._conn.commit()
            return cur.rowcount

    def list_recent(self, limit: int = 100) -> list[Job]:
        """給 admin History 頁用：最近的 job，新的在前。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,status,params,clips,error,error_message,"
                "created_at,started_at,finished_at FROM jobs"
                " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [
            Job(id=r[0], status=r[1], params=json.loads(r[2]),
                clips=[Clip(**c) for c in json.loads(r[3])],
                error=r[4], error_message=r[5],
                created_at=r[6], started_at=r[7], finished_at=r[8])
            for r in rows
        ]


class GenerationError(Exception):
    """runner 拋的可分類錯誤，code 會進 job.error"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


class QueueFullError(Exception):
    pass


Runner = Callable[["Job"], Awaitable[list[Clip]]]


class JobQueue:
    """單 worker：一次跑一單，其餘排隊。"""

    def __init__(self, store: JobStore, runner: Runner, *,
                 max_size: int, default_timeout: int,
                 generated_dir: str, retention_days: int) -> None:
        self._store = store
        self._runner = runner
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_size)
        self._default_timeout = default_timeout
        self._generated_dir = generated_dir
        self._retention_days = retention_days

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def submit(self, params: dict) -> Job:
        if self._queue.full():
            raise QueueFullError()
        job = self._store.create(params)
        self._queue.put_nowait(job.id)
        return job

    async def worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            job: Job | None = None
            try:
                job = self._store.get(job_id)
                if job is None:
                    continue
                job.status = "generating"
                job.started_at = time.time()
                self._store.save(job)
                cleanup_expired(self._generated_dir, self._retention_days)
                timeout = int(job.params.get("timeout") or self._default_timeout)
                try:
                    clips = await asyncio.wait_for(self._runner(job), timeout=timeout)
                    job.clips = clips
                    if not any(c.downloadable for c in clips):
                        raise GenerationError("download_failed", "一首可下載的都沒有")
                    job.status = "done"
                except GenerationError as e:
                    job.status = "error"
                    job.error = e.code
                    job.error_message = e.message or None
                except asyncio.TimeoutError:
                    job.status = "error"
                    job.error = "generation_timeout"
                except Exception as e:  # 意料外的一律歸 browser_error
                    job.status = "error"
                    job.error = "browser_error"
                    job.error_message = str(e)[:500]
                job.finished_at = time.time()
                self._store.save(job)
            except Exception as e:
                # job 可能連 self._store.get() 都還沒成功（例如暫時性 sqlite
                # 錯誤），此時沒有 job 物件可以標記失敗，只能放過這一筆、讓
                # 迴圈繼續存活等下一筆，不能讓 worker coroutine 整個掛掉
                # （否則佇列從此永久卡死，需要重啟服務才能恢復）。
                if job is not None:
                    job.status = "error"
                    job.error = "browser_error"
                    job.error_message = str(e)[:500]
                    job.finished_at = time.time()
                    try:
                        self._store.save(job)
                    except Exception:
                        pass


def cleanup_expired(generated_dir: str, retention_days: int) -> None:
    """刪掉超過保留天數的 job 目錄。生成前順手呼叫，不另開排程。"""
    root = Path(generated_dir)
    if not root.is_dir():
        return
    cutoff = time.time() - retention_days * 86400
    for child in root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
