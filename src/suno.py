"""Suno 頁面流程：寫入走 UI、讀取走網路側錄"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from . import selectors
from .browser import BrowserManager
from .config import Settings
from .jobs import Clip, GenerationError, Job
from .tagging import tag_mp3

TERMINAL_STATUSES = {"complete", "error"}
_MAX_TRACKED_CLIPS = 500
# 判斷 clip 是不是這次 job 才產生的容錯窗口（秒）
_CLOCK_SKEW_TOLERANCE = 180.0


@dataclass
class RawClip:
    id: str
    title: str = ""
    status: str = ""
    duration: float | None = None
    audio_url: str | None = None
    image_url: str | None = None
    created_at: str | None = None  # Suno 回傳的 ISO8601 UTC 字串（見 _wait_new_ids）
    lyrics: str = ""               # Suno 把歌詞放在 metadata.prompt
    tags: str = ""                 # 曲風，Suno 放在 metadata.tags


def parse_feed_payload(payload: Any) -> list[RawClip]:
    """在任意巢狀 JSON 裡撈出 clip 物件（有 id + status 字串的 dict）。
    容錯設計：Suno 改包裝層不影響，只要 clip 本體還有 id/status。"""
    found: dict[str, RawClip] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            cid = node.get("id") or node.get("clip_id")
            st = node.get("status") or node.get("state")
            if isinstance(cid, str) and cid:
                if not isinstance(st, str):
                    st = "submitted"
                meta = node.get("metadata")
                duration = node.get("duration")
                if duration is None and isinstance(meta, dict):
                    duration = meta.get("duration")
                created_at = node.get("created_at") or node.get("created_at_utc")
                if not created_at and isinstance(meta, dict):
                    created_at = meta.get("created_at") or meta.get("created_at_utc")
                found[cid] = RawClip(
                    id=cid,
                    title=str(node.get("title") or ""),
                    status=str(st),
                    duration=float(duration) if duration else None,
                    audio_url=node.get("audio_url") or None,
                    image_url=node.get("image_url") or None,
                    created_at=created_at if isinstance(created_at, str) else None,
                    lyrics=(meta.get("prompt") or "") if isinstance(meta, dict) else "",
                    tags=(meta.get("tags") or "") if isinstance(meta, dict) else "",
                )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return list(found.values())


def extract_credits(payload: Any) -> int | None:
    """優先算免費方案真正剩餘量（monthly_limit - monthly_usage）：對 free tier
    帳號，CREDITS_JSON_KEYS 指到的 "credits" 欄位其實是另外購買的點數包餘額，
    恆為 0（見 selectors.py 對 CREDITS_URL_SUBSTRINGS 的偵察筆記），使用者真正
    在意的「這個月還能生成幾首」要用 monthly_limit 減 monthly_usage。這兩個
    欄位同一份 payload 就有，算起來零成本，優先用；算不出來（欄位缺漏、型別
    不對，例如付費方案可能沒有月配額概念）才退回原本字面 key 查找。"""
    if not isinstance(payload, dict):
        return None
    limit = payload.get(selectors.CREDITS_MONTHLY_LIMIT_KEY)
    usage = payload.get(selectors.CREDITS_MONTHLY_USAGE_KEY)
    if isinstance(limit, (int, float)) and isinstance(usage, (int, float)):
        return int(limit) - int(usage)
    for key in selectors.CREDITS_JSON_KEYS:
        val = payload.get(key)
        if isinstance(val, (int, float)):
            return int(val)
    return None


def _parse_epoch(created_at: str | None) -> float | None:
    """把 clip 的 created_at（ISO8601 UTC，例如 "2026-08-20T10:00:00.000Z"）
    轉成 epoch 秒數，供 _wait_new_ids 判斷「這筆是不是這次 job 才生出來的」。
    解析失敗（欄位缺漏、型別不對、或格式跟預期不同）回 None，呼叫端要能
    容錯——型別檢查特別列出來是因為這個值來自對方 JSON payload，型別標註
    是 str | None 只是我方期待，不是保證，防禦一下避免 .replace() 對非
    字串值炸 AttributeError。"""
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class SunoRunner:
    """把一個 job 跑完：填表單、按 Create、側錄 feed、下載音檔。"""

    def __init__(self, browser: BrowserManager, settings: Settings) -> None:
        self._browser = browser
        self._settings = settings
        self._clips: dict[str, RawClip] = {}
        self._sniffing = False
        self._sniffer_tasks: set = set()
        self.logged_in: bool | None = None
        self.last_credits: int | None = None
        # Suno 按下 Create 前會先打 /api/c/check 問要不要驗證碼。
        #
        # **這個值不能拿來判定失敗。** 2026-08-27 實測：真人開的、當下生得
        # 出歌的瀏覽器，打同一支端點也回 required:true。它是所有人的常態，
        # 不是「這個帳號被舉牌」。舊版把「等不到新 clip」＋「required:true」
        # 報成 captcha_required，等於任何原因的失敗都被貼上驗證碼的標籤，
        # 害人往「養帳號信任度」的方向查了兩天。留著這個欄位只為了觀測。
        self.captcha_required: bool | None = None
        # 按下 Create 之後，前端有沒有真的把生成請求送出去。
        # 斷在 check 與 generate 之間（Turnstile 解不出 token）跟送出去了但
        # feed 沒回新 clip，是兩種完全不同的故障，錯誤訊息要分得出來。
        self.generate_submitted: bool = False
        # 攔到的 Turnstile 客戶端錯誤（例如 300010）。有值才代表真的是驗證碼壞掉。
        self.turnstile_errors: list[str] = []

    # ---- 側錄 ----

    def _install_sniffer(self, page: Page) -> None:
        """asyncio.create_task() 建立的 task 若沒有任何地方保留參照，事件迴圈
        只認一個弱參照，垃圾回收有機會在 task 跑到一半時就把它清掉、任務
        中止但不拋錯，等於側錄悄悄漏資料。用 self._sniffer_tasks 這個 set
        保留強參照，跑完（或出例外）用 done-callback 自行從 set 移除。"""
        if self._sniffing:
            return

        def _handle(response) -> None:
            task = asyncio.create_task(self._on_response(response))
            self._sniffer_tasks.add(task)
            task.add_done_callback(self._sniffer_tasks.discard)

        def _on_request(request) -> None:
            # 只要有非 /api/c/check 的 POST 請求發出，即代表 Create 表單請求已送出
            if request.method == "POST" and "/api/c/check" not in request.url and "/log" not in request.url:
                self.generate_submitted = True

        def _on_console(msg) -> None:
            text = msg.text or ""
            if "Turnstile" in text and ("Error" in text or "error" in text):
                self.turnstile_errors.append(text[:200])

        page.on("response", _handle)
        page.on("request", _on_request)
        page.on("console", _on_console)
        self._sniffing = True

    async def _on_response(self, response) -> None:
        url = response.url
        if "/api/c/check" in url:
            try:
                body = await response.json()
            except Exception:
                body = {}
            if isinstance(body, dict) and "required" in body:
                self.captcha_required = bool(body["required"])
        is_credits = any(s in url for s in selectors.CREDITS_URL_SUBSTRINGS)
        try:
            payload = await response.json()
        except Exception:
            return
        found_clips = parse_feed_payload(payload)
        if found_clips:
            for rc in found_clips:
                self._clips[rc.id] = rc
            while len(self._clips) > _MAX_TRACKED_CLIPS:
                self._clips.pop(next(iter(self._clips)))
        if is_credits:
            credits = extract_credits(payload)
            if credits is not None:
                self.last_credits = credits

    # ---- 主流程 ----

    async def run(self, job: Job) -> list[Clip]:
        page = self._browser.page
        self._install_sniffer(page)
        await self._ensure_on_create_page(page)
        before = set(self._clips.keys())
        self.generate_submitted = False
        self.turnstile_errors.clear()
        await self._fill_form(page, job.params)
        # Task 11 實機踩到的坑：帳號的 feed（含歷史舊 clip）常常不是在頁面剛
        # load 完就側錄得到，而是要等到按下 Create 之後、Suno 前端才一次把
        # 「整份清單」重新打過來——這代表上面 before 這個快照常常是空的或不
        # 完整，若只靠「id 不在 before 裡」判斷新舊，帳號歷史上幾十首舊歌會
        # 被整批誤判成這次 job 剛生出來的，連帶被誤下載。改記錄「按 Create
        # 前」的時間點，_wait_new_ids 用 clip 的 created_at（Suno 伺服器時間）
        # 是否晚於這個時間點來判斷才是這次 job 真正生出來的，不再單靠
        # before 集合。
        submit_time = time.time()
        await self._click_create(page)
        new_ids = await self._wait_new_ids(before, submit_time, page=page)
        raws = await self._wait_terminal(page, new_ids)
        return await self._download_all(job.id, raws)

    async def _ensure_on_create_page(self, page: Page) -> None:
        """每個 job 開始一定重新 goto 一次：拿到全新表單（Simple 分頁、純音樂
        關閉、欄位皆空的預設狀態），同時清掉上一個 job 殘留的表單狀態。
        Controller ruling 1：刻意蓋掉 brief 原本「if not page.url.startswith(...)
        才導覽」的條件式邏輯，因為那樣同一個 page 物件在跑第二個 job 時可能
        還停在第一個 job 填到一半、甚至已按過 Create 的頁面上，狀態不乾淨。"""
        try:
            await page.goto(self._settings.suno_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await page.goto(self._settings.suno_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        if await page.locator(selectors.LOGGED_OUT_MARKER).count() > 0:
            self.logged_in = False
            raise GenerationError("not_logged_in", "Suno chưa đăng nhập, vui lòng chạy 'suno-web login' trước.")
        self.logged_in = True

    async def _fill_form(self, page: Page, params: dict) -> None:
        try:
            if params["mode"] == "custom":
                custom_tab = page.locator(selectors.CUSTOM_TAB).first
                if await custom_tab.count() > 0:
                    try:
                        await custom_tab.click(timeout=3000)
                    except Exception:
                        pass
                await self._set_lyrics_mode(page, bool(params.get("instrumental")))
                if params["lyrics"]:
                    lyr = page.locator(selectors.LYRICS_TEXTAREA).first
                    if await lyr.count() > 0:
                        await lyr.fill(params["lyrics"])
                if params["style"]:
                    style_el = page.locator(selectors.STYLES_INPUT).first
                    if await style_el.count() == 0:
                        style_el = page.locator('textarea[placeholder*="style"], textarea[placeholder*="Style"], textarea[maxlength="1000"]').first
                    if await style_el.count() > 0:
                        await style_el.fill(params["style"])
                if params["title"]:
                    title_el = page.locator(selectors.TITLE_INPUT).last
                    if await title_el.count() > 0:
                        await title_el.fill(params["title"])
            else:
                simple_tab = page.locator(selectors.SIMPLE_TAB).first
                if await simple_tab.count() > 0:
                    try:
                        await simple_tab.click(timeout=3000)
                    except Exception:
                        pass
                prompt_el = page.locator(selectors.PROMPT_TEXTAREA).first
                if await prompt_el.count() == 0:
                    prompt_el = page.locator('textarea[placeholder*="song"], textarea[placeholder*="Song"], textarea[maxlength="3000"]').first
                if await prompt_el.count() > 0:
                    await prompt_el.fill(params["prompt"])
                    try:
                        await prompt_el.dispatch_event("input")
                        await prompt_el.dispatch_event("change")
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)
                if params.get("instrumental"):
                    toggle = page.locator(selectors.INSTRUMENTAL_TOGGLE).first
                    if await toggle.count() > 0:
                        try:
                            await toggle.click(timeout=3000)
                        except Exception:
                            pass
        except GenerationError:
            raise
        except Exception as e:
            raise GenerationError("submit_failed",
                                  f"Thao tác trên biểu mẫu thất bại (Selector có thể đã quá hạn): {e}") from e

    async def _set_lyrics_mode(self, page: Page, want_instrumental: bool) -> None:
        target_selector = (selectors.LYRICS_MODE_INSTRUMENTAL if want_instrumental
                           else selectors.LYRICS_MODE_WRITE)
        radio = page.locator(target_selector).first
        if await radio.count() == 0:
            return
        try:
            await radio.scroll_into_view_if_needed(timeout=2000)
            if await radio.get_attribute("aria-checked") == "true":
                return
            handle = await radio.element_handle()
            if handle:
                await page.evaluate("el => el.click()", handle)
                await page.wait_for_timeout(300)
        except Exception:
            pass

    async def _click_create(self, page: Page) -> None:
        candidates = [
            selectors.CREATE_BUTTON,
            '[aria-label="Create song"]',
            '[aria-label="Create"]',
            'button:has-text("Create")',
            'button[type="submit"]',
        ]
        for sel in candidates:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                try:
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    handle = await btn.element_handle()
                    if handle:
                        await page.evaluate("el => el.click()", handle)
                        await page.wait_for_timeout(1000)
                        return
        raise GenerationError("submit_failed", "Không nhấn được nút Create (Không tìm thấy nút Create trên trang Suno)")

    def _is_freshly_created(self, clip_id: str, before: set[str],
                            submit_time: float) -> bool:
        rc = self._clips.get(clip_id)
        if rc is None:
            return False
        epoch = _parse_epoch(rc.created_at)
        if epoch is not None:
            return epoch >= submit_time - _CLOCK_SKEW_TOLERANCE
        return clip_id not in before

    async def _wait_new_ids(self, before: set[str], submit_time: float,
                            timeout: float = 150.0, page: Page | None = None) -> set[str]:
        deadline = time.time() + timeout
        last_reload = time.time()
        while time.time() < deadline:
            new = {cid for cid in self._clips
                   if self._is_freshly_created(cid, before, submit_time)}
            if new:
                await asyncio.sleep(3)
                return {cid for cid in self._clips
                        if self._is_freshly_created(cid, before, submit_time)}
            if page is not None and time.time() - last_reload >= 15.0:
                last_reload = time.time()
                try:
                    await page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
            await asyncio.sleep(1.0)
        if self.generate_submitted:
            raise GenerationError(
                "submit_failed",
                "Yêu cầu tạo nhạc đã được gửi nhưng không xuất hiện bài hát mới trong 90 giây. "
                "Có thể do máy chủ Suno bị tắc nghẽn hoặc gói tin bị bỏ lỡ.")
        if self.turnstile_errors:
            raise GenerationError(
                "captcha_unsolved",
                "Giao diện bị nghẽn tại Cloudflare Turnstile và không giải được token nên yêu cầu chưa gửi đi được. "
                f"Lỗi thu thập: {self.turnstile_errors[-1]}.")
        raise GenerationError(
            "submit_failed",
            "Đã nhấn Create nhưng không có yêu cầu tạo nhạc nào được gửi đi. "
            f"(Tham khảo: /api/c/check trả về required={self.captcha_required})")

    async def _wait_terminal(self, page: Page, ids: set[str],
                             refresh_interval: float = 20.0) -> list[RawClip]:
        # 整體 timeout 由 JobQueue 的 asyncio.wait_for 控，這裡只管輪詢。
        #
        # Task 11 實機踩到的坑：剛按下 Create 後，Suno 前端通常只主動打
        # 一兩次 /api/feed/v3（一次帶出帳號歷史、一次帶出新 clip 剛進
        # streaming 狀態），之後就不再重打這隻 API 了——真正的
        # streaming -> complete 轉換，前端另外走什麼即時管道（很可能是
        # WebSocket / SSE）我們的 response sniffer 攔不到（Playwright 的
        # page.on("response") 只看得到 HTTP 回應）。純被動側錄會導致這裡
        # 永遠等不到終態，只能被外層 JobQueue 的 600s job timeout 強制中止
        # ——即使歌曲其實已經在 Suno 那邊生成完成。實測（2026-08-20）：兩個
        # 新 clip 卡在 streaming 超過 560 秒都沒等到任何新的 feed 回應，
        # 但用另一支腳本重新導覽頁面後立刻讀到兩者皆已 complete。修法：
        # 每隔 refresh_interval 秒主動 reload 一次頁面，強迫瀏覽器重新打
        # 一次 feed，才能真的觀察到狀態變化；reload 失敗（暫時性網路問題）
        # 不中止等待，下一輪再試。
        last_refresh = time.time()
        while True:
            raws = [self._clips[i] for i in ids if i in self._clips]
            if raws and all(r.status in TERMINAL_STATUSES for r in raws):
                return raws
            if time.time() - last_refresh >= refresh_interval:
                try:
                    await page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
                last_refresh = time.time()
            await asyncio.sleep(2)

    # ---- 下載 ----

    async def _download_all(self, job_id: str, raws: list[RawClip]) -> list[Clip]:
        out_dir = Path(self._settings.generated_dir) / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        clips: list[Clip] = []
        for rc in raws:
            clip = Clip(id=rc.id, title=rc.title, status=rc.status,
                        duration=rc.duration, lyrics=rc.lyrics)
            if rc.status == "complete" and rc.audio_url:
                if await self._download(rc.audio_url, out_dir / f"{rc.id}.mp3"):
                    clip.downloadable = True
                    clip.filename = f"{rc.id}.mp3"
                if rc.image_url and await self._download(
                        rc.image_url, out_dir / f"{rc.id}.jpeg"):
                    clip.image_filename = f"{rc.id}.jpeg"
                # Suno 的 CDN 原檔什麼標籤都沒有，自己補上。下游（CLI、API、
                # 呼叫端下載）拿到的就都是自帶歌名／曲風／歌詞／封面的檔案。
                if clip.downloadable:
                    tag_mp3(
                        out_dir / f"{rc.id}.mp3",
                        title=rc.title or "Suno",
                        album=rc.tags,
                        lyrics=rc.lyrics,
                        cover_path=(out_dir / clip.image_filename)
                        if clip.image_filename else None,
                    )
            clips.append(clip)
        return clips

    async def _download(self, url: str, dest: Path) -> bool:
        try:
            resp = await self._browser.context.request.get(url)
            if resp.status != 200:
                return False
            body = await resp.body()
            if len(body) < 1024:  # VIP 擋下的常是短錯誤頁，不是音檔
                return False
            dest.write_bytes(body)
            return True
        except Exception:
            return False
