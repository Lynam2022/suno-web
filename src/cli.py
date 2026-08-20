"""suno-web CLI"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="suno-web", description="Suno 網頁版自動化")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="安裝 Playwright Chromium")
    sub.add_parser("login", help="開瀏覽器人工登入 Suno")
    sub.add_parser("serve", help="啟動 HTTP API")
    p_health = sub.add_parser("health", help="檢查服務狀態")
    p_health.add_argument("--server", default="http://localhost:8071")
    g = sub.add_parser("generate", help="生成音樂（需要 serve 在跑）")
    g.add_argument("prompt", nargs="?", default="", help="Simple 模式描述")
    g.add_argument("--lyrics-file", help="Custom 模式：歌詞檔路徑")
    g.add_argument("--style", help="Custom 模式：曲風")
    g.add_argument("--title", help="Custom 模式：歌名")
    g.add_argument("--instrumental", action="store_true", help="純音樂")
    g.add_argument("-o", "--output", default=".", help="輸出目錄")
    g.add_argument("--server", default="http://localhost:8071")
    g.add_argument("--api-key", default=os.getenv("SUNO_WEB_API_KEY", ""))
    return parser


def _install() -> None:
    print("安裝 Playwright Chromium...")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                   check=True)
    print("完成。接著跑 suno-web login 登入 Suno。")


async def _login() -> None:
    from .browser import BrowserManager
    from .config import settings

    bm = BrowserManager(headless=False)
    await bm.start()
    await bm.page.goto(settings.suno_url)
    print(f"\n瀏覽器已開啟（profile: {settings.profile_dir}）")
    print("請登入 Suno 帳號，確認看得到 Create 頁面後，回到終端機按 Enter 關閉...")
    await asyncio.get_event_loop().run_in_executor(None, input)
    await bm.stop()
    print("登入狀態已儲存。")


def _serve() -> None:
    import uvicorn

    from .browser import BrowserManager
    from .config import settings
    from .jobs import JobQueue, JobStore
    from .main import create_app
    from .suno import SunoRunner

    browser = BrowserManager()
    runner = SunoRunner(browser, settings)
    store = JobStore(str(Path(settings.data_dir) / "jobs.db"))
    queue = JobQueue(
        store, runner.run,
        max_size=settings.queue_max_size,
        default_timeout=settings.default_timeout,
        generated_dir=settings.generated_dir,
        retention_days=settings.audio_retention_days,
    )
    app = create_app(
        settings=settings, store=store, queue=queue, browser=browser,
        health_extra=lambda: {
            "browser_alive": browser.is_alive(),
            "logged_in": runner.logged_in,
            "credits": runner.last_credits,
        },
    )
    uvicorn.run(app, host=settings.host, port=settings.port)


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key} if api_key else {}


def _health(args) -> None:
    import httpx

    try:
        resp = httpx.get(f"{args.server.rstrip('/')}/api/health", timeout=10)
    except httpx.ConnectError:
        print(f"連不上 {args.server} — 服務沒起來?先跑 suno-web serve")
        sys.exit(1)
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


def _generate(args) -> None:
    import httpx

    base = args.server.rstrip("/")
    headers = _headers(args.api_key)
    body: dict = {"instrumental": args.instrumental}
    if args.lyrics_file or args.style:
        if args.lyrics_file:
            body["lyrics"] = Path(args.lyrics_file).read_text(encoding="utf-8")
        if args.style:
            body["style"] = args.style
        if args.title:
            body["title"] = args.title
    else:
        if not args.prompt:
            print("要嘛給 prompt，要嘛給 --lyrics-file / --style")
            sys.exit(2)
        body["prompt"] = args.prompt

    try:
        resp = httpx.post(f"{base}/api/generate", json=body, headers=headers,
                          timeout=30)
    except httpx.ConnectError:
        print(f"連不上 {args.server} — 服務沒起來?先跑 suno-web serve")
        sys.exit(1)
    if resp.status_code != 200:
        print(f"送單失敗 {resp.status_code}: {resp.text}")
        sys.exit(1)
    job_id = resp.json()["job_id"]
    print(f"job {job_id} 已送出，等生成（通常 2-4 分鐘）...")

    while True:
        time.sleep(5)
        try:
            job = httpx.get(f"{base}/api/jobs/{job_id}", headers=headers,
                            timeout=30).json()
        except httpx.ConnectError:
            print("連線中斷,5 秒後重試...")
            continue
        if job["status"] in ("done", "error"):
            break
        print(f"  {job['status']}...")

    if job["status"] == "error":
        print(f"失敗：{job['error']} {job.get('error_message') or ''}")
        sys.exit(1)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    for clip in job["clips"]:
        if not clip.get("downloadable"):
            print(f"  跳過（不可下載）：{clip.get('title') or clip['id']}")
            continue
        audio = httpx.get(f"{base}{clip['audio_url']}", headers=headers,
                          timeout=120)
        if audio.status_code != 200:
            print(f"  下載失敗（HTTP {audio.status_code}）：{clip.get('title') or clip['id']}")
            continue
        dest = out / f"{clip['id']}.mp3"
        dest.write_bytes(audio.content)
        print(f"  已存：{dest}（{clip.get('duration')}s）")


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "install":
        _install()
    elif args.cmd == "login":
        asyncio.run(_login())
    elif args.cmd == "serve":
        _serve()
    elif args.cmd == "health":
        _health(args)
    elif args.cmd == "generate":
        _generate(args)
