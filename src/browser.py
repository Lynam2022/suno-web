"""瀏覽器管理：自己啟動真 Chrome，再用 CDP 接上去。

為什麼不讓 Playwright 直接啟動瀏覽器（2026-08-20 實測）：Suno 對生成動作
開了 Cloudflare Turnstile 驗證，Playwright 啟動的瀏覽器（不論 channel 是
chromium 還是 chrome）`navigator.webdriver` 都是 true，Turnstile 會跳出要
人點的勾選框，生成請求根本送不出去。改成自己起一個真 Chrome、再用 CDP
接上去之後 `navigator.webdriver` 是 false，Turnstile 靜默通過，生成正常。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import settings

# Chrome 啟動後會把實際的 debug port 寫進 profile 目錄的這個檔案（第一行）。
_PORT_FILE = "DevToolsActivePort"
_PORT_WAIT_SECONDS = 40.0


class BrowserManager:
    def __init__(self, headless: bool | None = None,
                 profile_dir: str | None = None,
                 chrome_binary: str | None = None) -> None:
        self._headless = settings.headless if headless is None else headless
        self._profile_dir = profile_dir or settings.profile_dir
        self._chrome_binary = chrome_binary or settings.chrome_binary
        self._no_sandbox = settings.chrome_no_sandbox
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._proc: subprocess.Popen | None = None
        self._stderr_path: Path | None = None
        self._stderr_file = None

    def _resolve_chrome(self) -> str:
        found = shutil.which(self._chrome_binary)
        if found:
            return found
        if Path(self._chrome_binary).is_file():
            return self._chrome_binary
        raise RuntimeError(
            f"找不到 Chrome 執行檔「{self._chrome_binary}」。請安裝 Google Chrome，"
            "或用環境變數 CHROME_BINARY 指到執行檔的完整路徑。"
            "不要改用 Playwright 內建的 Chromium，那個過不了 Suno 的 Turnstile 驗證。"
        )

    async def start(self) -> None:
        chrome = self._resolve_chrome()
        profile = Path(self._profile_dir)
        profile.mkdir(parents=True, exist_ok=True)
        port_file = profile / _PORT_FILE
        port_file.unlink(missing_ok=True)

        args = [
            chrome,
            # port 交給系統挑（0），實際值由 Chrome 寫進 DevToolsActivePort。
            # 固定 port 會讓將來多帳號同時開的時候互相搶，這裡刻意不固定。
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=zh-TW",
            # cookie 用固定金鑰加密，不要走系統鑰匙圈。桌機有 gnome-keyring
            # 時 Chrome 會用鑰匙圈的金鑰，那份 profile 複製到別台機器就解不開
            # cookie，登入態等於消失（實測把 profile 送到 .11 就變成未登入）。
            # Playwright 啟動瀏覽器時本來就帶這個旗標，我們自己接手啟動之後
            # 要記得補回來。
            "--password-store=basic",
        ]
        if self._headless:
            args.append("--headless=new")
        if self._no_sandbox:
            args.append("--no-sandbox")
        # Chrome 的 stderr 留著：它啟動失敗時的原因只寫在這裡（例如 sandbox
        # 沒設定好），丟掉的話錯誤訊息只能用猜的。
        self._stderr_path = profile / "chrome-stderr.log"
        self._stderr_file = self._stderr_path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=self._stderr_file)

        port = await self._wait_for_port(port_file)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}")
        self._context = (self._browser.contexts[0] if self._browser.contexts
                         else await self._browser.new_context())
        self._page = (self._context.pages[0] if self._context.pages
                      else await self._context.new_page())

    async def _wait_for_port(self, port_file: Path) -> int:
        """等 Chrome 寫出 DevToolsActivePort。等不到就把它收乾淨再報錯。"""
        deadline = asyncio.get_event_loop().time() + _PORT_WAIT_SECONDS
        while asyncio.get_event_loop().time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                await self._kill_process()
                raise RuntimeError(
                    "Chrome 啟動後立刻結束了。Chrome 自己說："
                    f"{self._read_stderr_tail()}"
                )
            if port_file.is_file():
                first_line = port_file.read_text(encoding="utf-8").splitlines()[:1]
                if first_line and first_line[0].strip().isdigit():
                    return int(first_line[0].strip())
            await asyncio.sleep(0.2)
        await self._kill_process()
        raise RuntimeError(
            f"等不到 Chrome 的 DevToolsActivePort（{_PORT_WAIT_SECONDS} 秒）。"
            f"Chrome 的輸出：{self._read_stderr_tail()}")

    def _read_stderr_tail(self, lines: int = 4) -> str:
        if self._stderr_file is not None:
            self._stderr_file.flush()
        if self._stderr_path is None or not self._stderr_path.is_file():
            return "（沒有輸出）"
        tail = self._stderr_path.read_text(encoding="utf-8",
                                           errors="replace").splitlines()[-lines:]
        return " / ".join(t.strip() for t in tail) or "（沒有輸出）"

    async def _kill_process(self) -> None:
        """只終止自己起的那一個 Chrome，不掃全域，多帳號才不會互相波及。"""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except Exception:
                pass
            self._stderr_file = None
        proc.terminate()
        for _ in range(50):
            if proc.poll() is not None:
                return
            await asyncio.sleep(0.1)
        proc.kill()

    async def stop(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        self._context = None
        self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        await self._kill_process()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("browser not started")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("browser not started")
        return self._context

    def is_alive(self) -> bool:
        return (self._page is not None and not self._page.is_closed()
                and self._proc is not None and self._proc.poll() is None)
