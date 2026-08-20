"""偵察 Suno 頁面：傾印 JSON 回應與 DOM 摘要，人工判讀後填 src/selectors.py。
用法：uv run python scripts/probe.py [--headed] [--seconds 30]
需先 suno-web login。輸出在 probe-out/（已 gitignore）。

延伸自 task-9-brief 的最小版：多做一次「點 Advanced 分頁後再抓 DOM」，
因為 Lyrics/Styles/Title 輸入框只在 Advanced 分頁才會出現在 DOM 上。
不會按 Create（怕燒點數），純觀察。"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser import BrowserManager
from src.config import settings

OUT = Path("probe-out")

DOM_SUMMARY_JS = """() =>
  [...document.querySelectorAll(
      'textarea,input,button,[role=switch],[role=tab]')].map(el => ({
    tag: el.tagName, role: el.getAttribute('role'),
    text: (el.textContent || '').trim().slice(0, 60),
    placeholder: el.getAttribute('placeholder'),
    aria: el.getAttribute('aria-label'),
    checked: el.getAttribute('aria-checked'),
    testid: el.getAttribute('data-testid'),
    selected: el.getAttribute('aria-selected'),
  }))"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    bm = BrowserManager(headless=not args.headed)
    await bm.start()
    page = bm.page
    seen: list[dict] = []

    async def on_response(resp) -> None:
        if "json" not in resp.headers.get("content-type", ""):
            return
        try:
            body = await resp.text()
        except Exception:
            return
        seen.append({"url": resp.url, "status": resp.status,
                     "body": body[:4000]})

    page.on("response", lambda r: asyncio.create_task(on_response(r)))
    await page.goto(settings.suno_url, wait_until="domcontentloaded")
    await asyncio.sleep(args.seconds)

    (OUT / "responses.json").write_text(
        json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "page.html").write_text(await page.content(), encoding="utf-8")
    summary = await page.evaluate(DOM_SUMMARY_JS)
    (OUT / "dom-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[Simple 分頁] 已寫入 probe-out/responses.json、dom-summary.json，"
          f"共 {len(seen)} 個 JSON 回應")

    # ---- 額外步驟：點 Advanced 分頁，抓 Lyrics/Styles/Title 輸入框 ----
    # 用 role=tab 且文字含 Advanced 找分頁鈕（不確定確切 selector，先用寬鬆條件探）
    advanced_tab = page.get_by_role("tab", name="Advanced")
    clicked = False
    try:
        if await advanced_tab.count() > 0:
            await advanced_tab.first.click()
            await page.wait_for_timeout(1500)
            clicked = True
    except Exception as e:
        print(f"點 Advanced 分頁失敗：{e}")

    if clicked:
        (OUT / "page-advanced.html").write_text(
            await page.content(), encoding="utf-8")
        summary_adv = await page.evaluate(DOM_SUMMARY_JS)
        (OUT / "dom-summary-advanced.json").write_text(
            json.dumps(summary_adv, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print("[Advanced 分頁] 已寫入 probe-out/dom-summary-advanced.json、"
              "page-advanced.html")
    else:
        print("找不到 Advanced 分頁按鈕，probe-out/dom-summary-advanced.json 未產生")

    # ---- 額外步驟：嘗試找 Sign in 之類的登出態標記文字（純觀察，不代表登出）----
    signin_candidates = ["Sign in", "Sign In", "Log in", "Log In"]
    signin_report = {}
    for text in signin_candidates:
        try:
            cnt = await page.get_by_text(text, exact=False).count()
        except Exception:
            cnt = None
        signin_report[text] = cnt
    (OUT / "signin-marker-counts.json").write_text(
        json.dumps(signin_report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"登入態標記文字出現次數（應接近 0，因為目前是已登入態）：{signin_report}")

    await bm.stop()


asyncio.run(main())
