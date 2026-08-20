"""HTTP API — job 式音樂生成服務"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import Settings
from . import admin_db
from .admin import create_admin_router
from .jobs import JobQueue, JobStore, QueueFullError
from .security import is_authorized, verify_admin_session

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class GenerateRequest(BaseModel):
    prompt: str | None = None
    lyrics: str | None = None
    style: str | None = None
    title: str | None = None
    instrumental: bool = False
    timeout: int | None = Field(default=None, ge=1)


def build_params(req: GenerateRequest, default_timeout: int) -> dict[str, Any]:
    prompt = (req.prompt or "").strip()
    lyrics = (req.lyrics or "").strip()
    style = (req.style or "").strip()
    title = (req.title or "").strip()
    custom = bool(lyrics or style)
    if not custom and not prompt:
        raise HTTPException(
            status_code=400,
            detail="invalid_request: prompt 或 lyrics/style 至少要有一個",
        )
    if lyrics and req.instrumental:
        raise HTTPException(
            status_code=400,
            detail="invalid_request: instrumental 與 lyrics 不能同時給（純音樂沒有歌詞欄）",
        )
    return {
        "mode": "custom" if custom else "simple",
        "prompt": prompt, "lyrics": lyrics, "style": style, "title": title,
        "instrumental": req.instrumental,
        "timeout": req.timeout or default_timeout,
    }


def create_app(*, settings: Settings, store: JobStore, queue: JobQueue,
               browser: Any = None,
               health_extra: Callable[[], dict[str, Any]] | None = None) -> FastAPI:
    started_at = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 服務重啟前卡在 queued/generating 的 job 永遠不會再被排到（queue 是
        # 純記憶體佇列），不作廢的話 client 會永遠輪詢不到終態。
        admin_db.init_db()
        store.fail_unfinished("服務重啟，重啟前未完成的 job 一律作廢")
        if browser is not None:
            await browser.start()
        worker = asyncio.create_task(queue.worker_loop())
        yield
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        if browser is not None:
            await browser.stop()

    app = FastAPI(lifespan=lifespan)

    def require_key(request: Request) -> str | None:
        """通過就回「這把金鑰的名稱」給 handler 記進 job；沒設任何金鑰時回 None。

        兩種來源都認：.env 的 API_KEYS（靜態、改了要重啟）與 admin 頁面現場
        發的動態金鑰（存雜湊在 admin.db）。只要其中一邊設了金鑰就強制驗證。
        """
        provided = request.headers.get("x-api-key")
        if is_authorized(provided, settings.api_keys) and not admin_db.has_any_dynamic_key():
            return None
        if provided and provided in settings.api_keys:
            return ".env 靜態金鑰"
        row = admin_db.get_api_key_by_token(provided) if provided else None
        if row and row["enabled"]:
            admin_db.mark_api_key_used(row["id"])
            return str(row["name"])
        raise HTTPException(status_code=403, detail="invalid_api_key")

    @app.post("/api/generate")
    async def generate(req: GenerateRequest,
                       key_name: str | None = Depends(require_key)) -> dict:
        params = build_params(req, settings.default_timeout)
        if key_name:
            params["api_key_name"] = key_name
        try:
            job = queue.submit(params)
        except QueueFullError:
            raise HTTPException(status_code=429, detail="queue_full")
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, _=Depends(require_key)) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="not_found")
        return job.to_api()

    def require_key_or_admin(request: Request):
        """音檔端點：API 呼叫端帶 x-api-key，管理台的瀏覽器則靠已登入的
        session cookie——歷史頁的連結是直接點開的，瀏覽器不會帶 header。"""
        if verify_admin_session(request.cookies.get("suno_admin"),
                                settings.admin_session_secret):
            return "admin"
        return require_key(request)

    @app.get("/api/jobs/{job_id}/files/{name}")
    async def get_file(job_id: str, name: str,
                       _=Depends(require_key_or_admin)) -> FileResponse:
        if not _JOB_ID_RE.match(job_id) or not _SAFE_NAME_RE.match(name):
            raise HTTPException(status_code=404, detail="not_found")
        path = Path(settings.generated_dir) / job_id / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(path)

    def admin_submit(*, prompt: str, lyrics: str, style: str, title: str,
                     instrumental: bool, key_name: str) -> str:
        """管理台測試頁送單：走跟 API 完全一樣的 build_params 與佇列，
        才不會出現「管理台測得過、API 卻不行」這種假象。"""
        req = GenerateRequest(prompt=prompt, lyrics=lyrics, style=style,
                              title=title, instrumental=instrumental)
        params = build_params(req, settings.default_timeout)
        params["api_key_name"] = key_name
        return queue.submit(params).id

    app.include_router(create_admin_router(
        settings=settings, store=store, queue=queue,
        started_at=started_at, health_extra=health_extra,
        submit=admin_submit))

    @app.get("/api/health")
    async def health() -> dict:
        info: dict[str, Any] = {
            "status": "ok",
            "queue_size": queue.queue_size,
            "uptime_seconds": round(time.time() - started_at, 1),
            "browser_alive": False,
            "logged_in": None,
            "credits": None,
        }
        if health_extra is not None:
            info.update(health_extra())
        return info

    return app
