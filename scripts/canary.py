#!/usr/bin/env python3
"""每天檢查這條路還活著沒，不生成、不扣點數；壞了就開 GitHub issue。

為什麼不照 gemini-web 那樣真生一張：suno 一單扣 10 點，帳號一個月只有
100 點。每天真生一單等於一個月燒 300 點，根本不可能。所以改成檢查「送得出
去之前的每一個前提」，這些前提壞掉正是實際發生過的故障：

  1. 登入態失效 —— 頁面變回未登入，之後每一單都 not_logged_in
  2. 點數用完 —— Create 按得下去但後端不排隊，job 以 submit_failed 收場
  3. selector 過期 —— Suno 改版，填不到欄位或按不到 Create
  4. 驗證碼被要求 —— /api/c/check 回 required:true 就代表會跳出要人點的
     Turnstile，生成會被擋（2026-08-20 就是這樣壞掉的，而且五小時後才發現）

第 4 點只有真的按下 Create 才問得到答案，所以這支不碰它，改成「頁面載入後
被動側錄到的那一次 check 結果」——沒側錄到就當作沒問題，不亂報。

    python3 scripts/canary.py            # 檢查，壞了才開 issue
    python3 scripts/canary.py --dry-run  # 只檢查，不開 issue
"""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import selectors  # noqa: E402
from src.browser import BrowserManager  # noqa: E402
from src.config import settings  # noqa: E402
from src.suno import SunoRunner  # noqa: E402

REPO = "yazelin/suno-web"
LABEL = "canary"
LOW_CREDITS = 10  # 剩這麼多以下就提醒：一單要 10 點，等於連一單都生不出來
SNAPSHOT = Path("/tmp/suno-canary-profile")
# 快取重建得回來，不用複製；佔了 profile 大部分體積。
_SKIP = shutil.ignore_patterns("Singleton*", "*.log", "Cache", "Code Cache",
                               "GPUCache", "DawnCache", "ShaderCache",
                               "component_crx_cache", "Crash Reports")


async def _start_browser() -> tuple[BrowserManager | None, str]:
    """先用正式 profile；被常駐服務佔用時改用快照副本。

    部署機上服務是 24 小時開著的，Chrome 對同一個 user-data-dir 只允許一個
    實例，所以金絲雀直接開會被擋。複製一份（跳過快取）就能用同一份登入態
    檢查，也不會動到正在跑的那個瀏覽器。
    """
    bm = BrowserManager(headless=True)
    try:
        await bm.start()
        return bm, "正式 profile"
    except Exception as e:
        if "ProcessSingleton" not in str(e) and "立刻結束" not in str(e):
            return None, f"瀏覽器起不來：{e}"
    shutil.rmtree(SNAPSHOT, ignore_errors=True)
    try:
        shutil.copytree(settings.profile_dir, SNAPSHOT, ignore=_SKIP,
                        symlinks=True, dirs_exist_ok=True)
        bm = BrowserManager(headless=True, profile_dir=str(SNAPSHOT))
        await bm.start()
        return bm, "快照副本（正式 profile 被服務佔用）"
    except Exception as e:
        return None, f"用快照副本也起不來：{e}"


async def check() -> list[str]:
    """回傳問題清單，空的就是一切正常。"""
    problems: list[str] = []
    bm, how = await _start_browser()
    if bm is None:
        return [how]
    print(f"（用 {how} 檢查）")

    runner = SunoRunner(bm, settings)
    captcha: list[str] = []

    async def watch(resp):
        if "/api/c/check" in resp.url:
            try:
                body = await resp.json()
            except Exception:
                return
            if body.get("required"):
                captcha.append(str(body))

    bm.page.on("response", lambda r: asyncio.create_task(watch(r)))
    # 點數是 runner 的側錄器在收的，導覽之前要先裝上，不然 last_credits
    # 永遠是 None，會被誤報成「讀不到點數」。
    runner._install_sniffer(bm.page)

    try:
        try:
            await runner._ensure_on_create_page(bm.page)
        except Exception as e:
            return [f"開不了 Create 頁或未登入：{e}"]

        # 這是 SPA，goto 完成不等於畫面畫好。先等 Create 鈕出現，再查其他
        # selector，不然會把「還沒 render」誤報成「Suno 改版」。
        try:
            await bm.page.wait_for_selector(selectors.CREATE_BUTTON,
                                            timeout=25000)
        except Exception:
            return ["等不到 Create 鈕出現（Suno 改版，或頁面載不出來）"]

        for name in ("SIMPLE_TAB", "PROMPT_TEXTAREA", "CREATE_BUTTON",
                     "CUSTOM_TAB", "INSTRUMENTAL_TOGGLE"):
            sel = getattr(selectors, name)
            if await bm.page.locator(sel).count() == 0:
                problems.append(f"selector 找不到元素：{name}（Suno 可能改版）")

        for _ in range(10):
            await bm.page.wait_for_timeout(2000)
            if runner.last_credits is not None:
                break
        if runner.last_credits is None:
            problems.append("讀不到點數（帳單端點沒被呼叫，或欄位換了）")
        elif runner.last_credits < LOW_CREDITS:
            problems.append(f"點數只剩 {runner.last_credits}，不夠生一單")

        if captcha:
            problems.append(f"生成被要求驗證碼：{captcha[0]}")
    finally:
        await bm.stop()
    return problems


def existing_issue() -> str:
    out = subprocess.run(
        ["gh", "issue", "list", "--repo", REPO, "--label", LABEL,
         "--state", "open", "--json", "number", "--jq", ".[0].number"],
        capture_output=True, text=True)
    return out.stdout.strip()


def open_issue(problems: list[str]) -> None:
    if existing_issue():
        print("已經有開著的 canary issue，不重複開")
        return
    body = ("金絲雀檢查發現問題（沒有生成，所以沒扣點數）：\n\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\n處理方式看 README 的「已知限制」與 `src/selectors.py` 的註解。")
    subprocess.run(
        ["gh", "issue", "create", "--repo", REPO, "--label", LABEL,
         "--title", "金絲雀：suno-web 生成前提檢查失敗", "--body", body],
        check=False)


def main() -> int:
    dry = "--dry-run" in sys.argv
    problems = asyncio.run(check())
    if not problems:
        print("正常：登入態、selector、點數都沒問題，也沒被要求驗證碼")
        return 0
    print("發現問題：")
    for p in problems:
        print(f"  - {p}")
    if not dry:
        open_issue(problems)
    return 1


if __name__ == "__main__":
    sys.exit(main())
