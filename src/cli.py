"""suno-web CLI"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _default_server() -> str:
    """服務位址預設吃 SUNO_WEB_SERVER，沒設才用本機。服務跑在別台機器時
    （例如部署在 .11），設一次環境變數就不用每個指令都打 --server。"""
    return os.getenv("SUNO_WEB_SERVER", "http://localhost:8071")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="suno-web", description="Suno 網頁版自動化")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="安裝 Playwright Chromium")
    p_login = sub.add_parser("login", help="開瀏覽器人工登入 Suno")
    p_login.add_argument("-w", "--worker", type=int, default=0,
                         help="第幾個帳號，預設 0")
    sub.add_parser("serve", help="啟動 HTTP API")
    p_health = sub.add_parser("health", help="檢查服務狀態")
    p_health.add_argument("--server", default=_default_server())
    g = sub.add_parser("generate", help="生成音樂（需要 serve 在跑）")
    g.add_argument("prompt", nargs="?", default="", help="Simple 模式描述")
    g.add_argument("--lyrics-file", help="Custom 模式：歌詞檔路徑")
    g.add_argument("--style", help="Custom 模式：曲風")
    g.add_argument("--title", help="Custom 模式：歌名")
    g.add_argument("--instrumental", action="store_true", help="純音樂")
    g.add_argument("-o", "--output", default=".", help="輸出目錄")
    g.add_argument("--server", default=_default_server())
    g.add_argument("--api-key", default=os.getenv("SUNO_WEB_API_KEY", ""))
    return parser


def _install_commands() -> None:
    """偵測 Claude Code 與 Gemini CLI，把 slash command 裝進去（同 gemini-web）。"""
    src = Path(__file__).parent / "commands"
    if not src.is_dir():
        return
    installed = []
    for home, ext, label in ((Path.home() / ".claude", "*.md", "Claude Code"),
                             (Path.home() / ".gemini", "*.toml", "Gemini CLI")):
        if not home.is_dir():
            continue
        dest = home / "commands" / "suno-web"
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.glob(ext):
            shutil.copy2(f, dest / f.name)
        installed.append(f"{label} → {dest}")
    if installed:
        print("slash command 已安裝：")
        for line in installed:
            print(f"  {line}")
        print("用法：/suno 想要什麼樣的音樂")


def _install() -> None:
    """檢查真 Chrome 在不在。不下載 Playwright 內建的 Chromium：本服務是
    自己啟動真 Chrome 再用 CDP 接上去，內建那顆過不了 Suno 的驗證。"""
    from .config import settings

    found = shutil.which(settings.chrome_binary)
    if found:
        print(f"找到 Chrome：{found}")
        _install_commands()
        print("接著跑 suno-web login 登入 Suno。")
        return
    print(f"找不到 Chrome 執行檔「{settings.chrome_binary}」。")
    print("Ubuntu 可以這樣裝：")
    print("  wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb")
    print("  sudo apt install ./google-chrome-stable_current_amd64.deb")
    print("沒有 root 的機器可以解到自己的目錄，再用環境變數指過去：")
    print("  dpkg-deb -x google-chrome-stable_current_amd64.deb ~/opt/chrome")
    print("  CHROME_BINARY=~/opt/chrome/opt/google/chrome/chrome")
    sys.exit(1)


def _login(worker: int = 0) -> None:
    """開一個普通的 Chrome 讓人登入，全程不接 CDP。

    為什麼不用 BrowserManager：那支會帶 --remote-debugging-port 再讓
    Playwright 接上去，而 Suno 的 Clerk 在登入流程掛了 Cloudflare Turnstile，
    被程式驅動的瀏覽器過不了那一關（實測 auth.suno.com/v1/client/verify 會
    收到 captcha_error=600010，畫面就變成 Initialization Error）。

    這不是 Google 的問題：Google 的登入頁在自動化瀏覽器裡照樣載入，所以
    gemini-web 那種只走 Google 的服務不受影響。登入這一步本來就不需要自動化，
    開起來、等人登完、等視窗關掉就好；服務要用時再接 CDP。
    """
    from .config import get_worker_profile_dir, settings

    chrome = shutil.which(settings.chrome_binary)
    if not chrome:
        print(f"找不到 Chrome 執行檔「{settings.chrome_binary}」，先跑 suno-web install")
        sys.exit(1)
    profile = Path(get_worker_profile_dir(worker))
    profile.mkdir(parents=True, exist_ok=True)

    print(f"帳號 {worker} 的 profile：{profile}")
    print("瀏覽器開好了。請在裡面登入 Suno（Google 或 email 都可以，這個視窗"
          "沒有被程式驅動）。")
    print("登完、確認看得到 Create 頁面之後，**把瀏覽器視窗關掉**，這裡就會"
          "自動驗證。不要在這裡按 Ctrl-C，那樣 cookie 可能沒寫回去。")
    proc = subprocess.Popen(
        [chrome, f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", settings.suno_url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait()
    print("視窗關掉了，驗證登入態……")
    asyncio.run(_verify_login(worker))


async def _verify_login(worker: int) -> None:
    """用無頭瀏覽器確認那份 profile 真的是登入狀態，順便讀點數。"""
    from .browser import BrowserManager
    from .config import get_worker_profile_dir, settings
    from .suno import SunoRunner

    bm = BrowserManager(headless=True,
                        profile_dir=get_worker_profile_dir(worker))
    try:
        await bm.start()
    except Exception as e:
        print(f"驗證時瀏覽器起不來：{e}")
        return
    runner = SunoRunner(bm, settings)
    try:
        runner._install_sniffer(bm.page)
        try:
            await runner._ensure_on_create_page(bm.page)
        except Exception as e:
            print(f"看起來還沒登入成功：{e}")
            return
        for _ in range(10):
            await bm.page.wait_for_timeout(2000)
            if runner.last_credits is not None:
                break
        credits = runner.last_credits
        print(f"帳號 {worker} 登入成功。"
              + (f"剩餘點數 {credits}（可生 {credits // 10} 單）"
                 if credits is not None else "點數讀不到，跑一單之後才會有值"))
    finally:
        await bm.stop()


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
        _login(args.worker)
    elif args.cmd == "serve":
        _serve()
    elif args.cmd == "health":
        _health(args)
    elif args.cmd == "generate":
        _generate(args)
