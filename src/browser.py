"""Playwright 瀏覽器管理（單 worker、persistent context）"""
from __future__ import annotations

from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from .config import settings


class BrowserManager:
    def __init__(self, headless: bool | None = None,
                 profile_dir: str | None = None) -> None:
        self._headless = settings.headless if headless is None else headless
        self._profile_dir = profile_dir or settings.profile_dir
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def start(self) -> None:
        Path(self._profile_dir).mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self._profile_dir,
            # channel="chromium"＝完整 chromium。gemini-web 在 .11 的教訓：
            # 不給 channel 會用 headless_shell 精簡殼，真實網站行為有差、
            # 過 bot 驗證能力也差，這裡直接沿用完整版。
            channel="chromium",
            headless=self._headless,
            locale="zh-TW",
            timezone_id=settings.stealth_timezone,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._page = (self._context.pages[0] if self._context.pages
                      else await self._context.new_page())

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._page = None

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
        return self._page is not None and not self._page.is_closed()
