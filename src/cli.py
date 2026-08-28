"""suno-web CLI - Công cụ dòng lệnh tự động hóa Suno Web"""
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
    """Địa chỉ dịch vụ mặc định từ SUNO_WEB_SERVER, nếu không cài đặt sẽ dùng localhost."""
    return os.getenv("SUNO_WEB_SERVER", "http://localhost:8071")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="suno-web", description="Tự động hóa phiên bản Suno Web")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="Kiểm tra và cấu hình trình duyệt Google Chrome")
    p_login = sub.add_parser("login", help="Mở trình duyệt để đăng nhập tài khoản Suno thủ công")
    p_login.add_argument("-w", "--worker", type=int, default=0,
                         help="Chỉ định số thứ tự tài khoản (mặc định 0)")
    sub.add_parser("serve", help="Khởi chạy dịch vụ HTTP API Server")
    p_health = sub.add_parser("health", help="Kiểm tra trạng thái hoạt động của dịch vụ")
    p_health.add_argument("--server", default=_default_server())
    g = sub.add_parser("generate", help="Tạo bài hát mới (yêu cầu dịch vụ serve đang chạy)")
    g.add_argument("prompt", nargs="?", default="", help="Mô tả bài hát (Chế độ Simple)")
    g.add_argument("--lyrics-file", help="Chế độ Custom: Đường dẫn tệp chứa lời bài hát")
    g.add_argument("--style", help="Chế độ Custom: Phong cách âm nhạc (Style)")
    g.add_argument("--title", help="Chế độ Custom: Tiêu đề bài hát")
    g.add_argument("--instrumental", action="store_true", help="Nhạc không lời (Instrumental)")
    g.add_argument("-o", "--output", default=".", help="Thư mục lưu tệp âm thanh (mặc định .)")
    g.add_argument("--server", default=_default_server())
    g.add_argument("--api-key", default=os.getenv("SUNO_WEB_API_KEY", ""))
    return parser


def _install_commands() -> None:
    """Tự động phát hiện Claude Code & Gemini CLI để cài đặt lệnh slash command."""
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
        print("Lệnh Slash Command đã được cài đặt:")
        for line in installed:
            print(f"  {line}")
        print("Cách dùng: /suno <mô tả bài hát bạn muốn tạo>")


def _install() -> None:
    """Kiểm tra sự tồn tại của Chrome chính thức."""
    from .browser import resolve_chrome_binary
    from .config import settings

    try:
        found = resolve_chrome_binary(settings.chrome_binary)
        print(f"Đã tìm thấy Chrome: {found}")
        _install_commands()
        print("Tiếp theo hãy chạy 'suno-web login' để đăng nhập tài khoản Suno.")
        return
    except RuntimeError:
        pass
    print(f"Không tìm thấy tệp thực thi Chrome «{settings.chrome_binary}».")
    print("Hướng dẫn cài đặt trên Ubuntu:")
    print("  wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb")
    print("  sudo apt install ./google-chrome-stable_current_amd64.deb")
    print("Nếu không có quyền root, giải nén vào thư mục cá nhân và đặt biến môi trường:")
    print("  dpkg-deb -x google-chrome-stable_current_amd64.deb ~/opt/chrome")
    print("  CHROME_BINARY=~/opt/chrome/opt/google/chrome/chrome")
    sys.exit(1)


def login_chrome_args(chrome: str, profile: str, settings) -> list[str]:
    """Các tham số khởi chạy Chrome khi đăng nhập."""
    args = [chrome, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--password-store=basic"]
    if settings.chrome_no_sandbox:
        args.append("--no-sandbox")
    args.append(settings.suno_url)
    return args


def _login(worker: int = 0) -> None:
    """Mở trình duyệt Chrome chuẩn để người dùng đăng nhập tài khoản Suno thủ công."""
    from .browser import resolve_chrome_binary
    from .config import get_worker_profile_dir, settings

    try:
        chrome = resolve_chrome_binary(settings.chrome_binary)
    except RuntimeError:
        print(f"Không tìm thấy tệp thực thi Chrome «{settings.chrome_binary}», vui lòng chạy 'suno-web install' trước.")
        sys.exit(1)
    profile = Path(get_worker_profile_dir(worker))
    profile.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32" and not os.getenv("DISPLAY") and not os.getenv("WAYLAND_DISPLAY"):
        print("Không có biến DISPLAY, không thể mở cửa sổ trình duyệt.")
        print("Nếu bạn đang kết nối từ xa, vui lòng dùng lệnh: ssh -X <ip-server> và thử lại.")
        sys.exit(1)

    print(f"Thư mục Profile của tài khoản {worker}: {profile}")
    print("Đã mở trình duyệt Chrome. Vui lòng đăng nhập Suno trong cửa sổ vừa mở.")
    print("Sau khi hoàn tất đăng nhập và nhìn thấy trang Create, **hãy đóng cửa sổ Chrome lại** để hệ thống tự động xác minh. Vui lòng không nhấn Ctrl-C.")
    args = login_chrome_args(chrome, str(profile), settings)
    err_path = profile / "chrome-login-stderr.log"
    started = time.time()
    with err_path.open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err)
        proc.wait()
    if time.time() - started < 5:
        tail = err_path.read_text(encoding="utf-8", errors="replace").strip()
        print("Trình duyệt đóng ngay sau khi mở. Thông báo chi tiết từ Chrome:")
        print(f"  {tail[-500:] or '(Không có kết xuất)'}")
        print("Nguyên nhân thường gặp: Profile này đang mở bởi một phiên Chrome khác hoặc thiếu chuyển tiếp X11 (dùng ssh -X).")
        sys.exit(1)
    print("Đã đóng cửa sổ trình duyệt, đang xác minh trạng thái đăng nhập...")
    asyncio.run(_verify_login(worker))


async def _verify_login(worker: int) -> None:
    """Xác minh trạng thái đăng nhập và kiểm tra điểm credits còn lại."""
    from .browser import BrowserManager
    from .config import get_worker_profile_dir, settings
    from .suno import SunoRunner

    bm = BrowserManager(headless=True,
                        profile_dir=get_worker_profile_dir(worker))
    try:
        await bm.start()
    except Exception as e:
        print(f"Lỗi khởi chạy trình duyệt khi xác minh: {e}")
        return
    runner = SunoRunner(bm, settings)
    try:
        runner._install_sniffer(bm.page)
        try:
            await runner._ensure_on_create_page(bm.page)
        except Exception as e:
            print(f"Có vẻ như tài khoản chưa được đăng nhập thành công: {e}")
            return
        for _ in range(10):
            await bm.page.wait_for_timeout(2000)
            if runner.last_credits is not None:
                break
        credits = runner.last_credits
        print(f"Tài khoản {worker} đăng nhập thành công."
              + (f" Số điểm còn lại: {credits} (tạo được khoảng {credits // 10} bài)"
                 if credits is not None else " Chưa đọc được số điểm, số điểm sẽ cập nhật sau khi tạo bài đầu tiên"))
    finally:
        await bm.stop()


def _serve() -> None:
    import uvicorn

    from .config import settings
    from .jobs import JobQueue, JobStore
    from .main import create_app
    from .worker_pool import WorkerPool

    pool = WorkerPool(settings)
    store = JobStore(str(Path(settings.data_dir) / "jobs.db"))
    queue = JobQueue(
        store, pool.runners,
        credits_of=pool.credits_of,
        dispatch_mode=settings.dispatch_mode,
        max_size=settings.queue_max_size,
        default_timeout=settings.default_timeout,
        generated_dir=settings.generated_dir,
        retention_days=settings.audio_retention_days,
    )
    app = create_app(settings=settings, store=store, queue=queue, pool=pool,
                     health_extra=pool.summary)
    print(f"Số lượng tài khoản (WORKER_COUNT): {pool.settings.worker_count}"
          f", Trình duyệt tự mở khi dùng và đóng sau {settings.idle_shutdown_minutes} phút không hoạt động.")
    uvicorn.run(app, host=settings.host, port=settings.port)


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key} if api_key else {}


def _health(args) -> None:
    import httpx

    try:
        resp = httpx.get(f"{args.server.rstrip('/')}/api/health", timeout=10)
    except httpx.ConnectError:
        print(f"Không thể kết nối đến {args.server} — Dịch vụ chưa chạy? Vui lòng chạy 'suno-web serve' trước.")
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
            print("Vui lòng cung cấp mô tả (prompt) hoặc truyền tham số --lyrics-file / --style")
            sys.exit(2)
        body["prompt"] = args.prompt

    try:
        resp = httpx.post(f"{base}/api/generate", json=body, headers=headers,
                          timeout=30)
    except httpx.ConnectError:
        print(f"Không thể kết nối đến {args.server} — Dịch vụ chưa chạy? Vui lòng chạy 'suno-web serve' trước.")
        sys.exit(1)
    if resp.status_code != 200:
        print(f"Gửi yêu cầu thất bại {resp.status_code}: {resp.text}")
        sys.exit(1)
    job_id = resp.json()["job_id"]
    print(f"Yêu cầu Job {job_id} đã được gửi thành công, đang chờ tạo nhạc (thường mất 2-4 phút)...")

    while True:
        time.sleep(5)
        try:
            job = httpx.get(f"{base}/api/jobs/{job_id}", headers=headers,
                            timeout=30).json()
        except httpx.ConnectError:
            print("Kết nối bị ngắt, đang thử lại sau 5 giây...")
            continue
        if job["status"] in ("done", "error"):
            break
        print(f"  Trạng thái: {job['status']}...")

    if job["status"] == "error":
        print(f"Tạo nhạc thất bại: {job['error']} {job.get('error_message') or ''}")
        sys.exit(1)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    for clip in job["clips"]:
        if not clip.get("downloadable"):
            print(f"  Bỏ qua (Chưa sẵn sàng tải xuống): {clip.get('title') or clip['id']}")
            continue
        audio = httpx.get(f"{base}{clip['audio_url']}", headers=headers,
                          timeout=120)
        if audio.status_code != 200:
            print(f"  Tải thất bại (HTTP {audio.status_code}): {clip.get('title') or clip['id']}")
            continue
        dest = out / f"{clip['id']}.mp3"
        dest.write_bytes(audio.content)
        print(f"  Đã lưu tệp: {dest} ({clip.get('duration')} giây)")


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


if __name__ == "__main__":
    main()

