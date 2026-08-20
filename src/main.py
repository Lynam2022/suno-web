"""HTTP API — job 式音樂生成服務"""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import Settings
from .jobs import JobQueue, JobStore, QueueFullError
from .security import is_authorized

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class GenerateRequest(BaseModel):
    prompt: str | None = None
    lyrics: str | None = None
    style: str | None = None
    title: str | None = None
    instrumental: bool = False
    timeout: int | None = None


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
        if browser is not None:
            await browser.start()
        worker = asyncio.create_task(queue.worker_loop())
        yield
        worker.cancel()
        if browser is not None:
            await browser.stop()

    app = FastAPI(lifespan=lifespan)

    def require_key(request: Request) -> None:
        if not is_authorized(request.headers.get("x-api-key"), settings.api_keys):
            raise HTTPException(status_code=403, detail="invalid_api_key")

    @app.post("/api/generate")
    def generate(req: GenerateRequest, _: None = Depends(require_key)) -> dict:
        params = build_params(req, settings.default_timeout)
        try:
            job = queue.submit(params)
        except QueueFullError:
            raise HTTPException(status_code=429, detail="queue_full")
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, _: None = Depends(require_key)) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="not_found")
        return job.to_api()

    @app.get("/api/jobs/{job_id}/files/{name}")
    def get_file(job_id: str, name: str,
                 _: None = Depends(require_key)) -> FileResponse:
        if not _JOB_ID_RE.match(job_id) or not _SAFE_NAME_RE.match(name):
            raise HTTPException(status_code=404, detail="not_found")
        path = Path(settings.generated_dir) / job_id / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(path)

    @app.get("/api/health")
    def health() -> dict:
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
