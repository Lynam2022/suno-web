"""Admin webui：登入、總覽、金鑰、歷史。

視覺規範（見 AGENTS.md）：深色底配珊瑚橘／洋紅，刻意跟 gemini-web 的淺色
靛藍分開，兩個服務的管理頁一眼要分得出來。

頁面是伺服器端算好的 HTML，沒有前端建置步驟。
"""
from __future__ import annotations

import html
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import admin_db
from .config import Settings
from .jobs import Job, JobQueue, JobStore
from .security import (constant_equals, create_admin_session,
                       verify_admin_session)

_COOKIE = "suno_admin"

_CSS = """
:root{
  --bg:#14100f; --panel:#1e1917; --line:#2f2724; --ink:#f2e9e4;
  --muted:#a9968d; --accent:#ff7a59; --accent2:#ff4d9d;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 system-ui,"Noto Sans TC",sans-serif}
header{display:flex;align-items:center;gap:20px;padding:14px 24px;
  background:linear-gradient(90deg,#241b18,#1a1513);border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:17px;letter-spacing:.5px}
header h1 span{background:linear-gradient(90deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;background-clip:text;color:transparent;font-weight:700}
nav{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
nav a{color:var(--muted);text-decoration:none;padding:6px 12px;border-radius:8px}
nav a:hover{color:var(--ink);background:#2a2320}
nav a.on{color:#14100f;background:linear-gradient(90deg,var(--accent),var(--accent2));font-weight:600}
main{max-width:1080px;margin:0 auto;padding:24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:18px 20px;margin-bottom:18px}
.card h2{margin:0 0 14px;font-size:15px;color:var(--muted);font-weight:600;
  letter-spacing:1px;text-transform:uppercase}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.stat{background:#191413;border:1px solid var(--line);border-radius:12px;padding:14px}
.stat b{display:block;font-size:22px;margin-bottom:2px}
.stat small{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);
  vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.6px;
  text-transform:uppercase}
code{background:#241d1b;padding:2px 6px;border-radius:6px;font-size:13px}
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;
  border:1px solid var(--line)}
.tag.done{color:var(--ok);border-color:#245c39}
.tag.err{color:var(--bad);border-color:#5c2424}
.tag.run{color:var(--warn);border-color:#5c4a24}
input[type=text],input[type=password]{background:#191413;border:1px solid var(--line);
  color:var(--ink);border-radius:9px;padding:10px 12px;font:inherit;min-width:220px}
input[type=text]:focus,input[type=password]:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(255,122,89,.18)}
input::placeholder{color:#7d6a62}
/* Chrome 自動填入會用自己的白底深字蓋掉上面的樣式,在深色卡片上很突兀。
   內陰影是唯一壓得住它背景的方法,文字顏色則要用 -webkit-text-fill-color。 */
input:-webkit-autofill,input:-webkit-autofill:hover,input:-webkit-autofill:focus{
  -webkit-text-fill-color:var(--ink);
  -webkit-box-shadow:0 0 0 1000px #191413 inset;
  caret-color:var(--ink);
  border:1px solid var(--line);
  transition:background-color 9999s ease-out 0s}
.login input{width:100%}
.login .row{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px}
.login .row input{width:auto;min-width:0}
button{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#14100f;
  border:0;border-radius:9px;padding:9px 16px;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--muted)}
form.inline{display:inline}
.new-key{background:#20302a;border:1px solid #2f5c46;border-radius:12px;
  padding:14px;margin-bottom:16px;word-break:break-all}
.muted{color:var(--muted)}
.login{max-width:340px;margin:14vh auto}
a.link{color:var(--accent)}
"""


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt_iso(value: str | None) -> str:
    """資料庫存的是 UTC ISO 字串，顯示要轉成本地時間（這台是 UTC+8）。"""
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%m-%d %H:%M")


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts))


def create_admin_router(*, settings: Settings, store: JobStore,
                        queue: JobQueue, started_at: float,
                        health_extra) -> APIRouter:
    router = APIRouter()
    prefix = settings.admin_url_prefix  # 反代到子路徑時的連結前綴

    def url(path: str) -> str:
        return f"{prefix}{path}"

    def _page(title: str, active: str, body: str) -> HTMLResponse:
        nav = "".join(
            f'<a class="{"on" if key == active else ""}" href="{url(href)}">{label}</a>'
            for key, href, label in (
                ("overview", "/admin", "總覽"),
                ("keys", "/admin/keys", "金鑰"),
                ("history", "/admin/history", "歷史"),
            ))
        return HTMLResponse(f"""<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} — suno-web</title><style>{_CSS}</style></head><body>
<header><h1><span>suno-web</span> 管理台</h1><nav>{nav}
<a href="{url('/admin/logout')}">登出</a></nav></header>
<main>{body}</main></body></html>""")

    def _current_user(request: Request) -> str | None:
        return verify_admin_session(request.cookies.get(_COOKIE),
                                    settings.admin_session_secret)

    def _login_redirect() -> RedirectResponse:
        return RedirectResponse(url("/admin/login"), status_code=303)

    # ---- 登入 ----

    @router.get("/admin/login", response_class=HTMLResponse)
    async def login_page(request: Request, err: str = "") -> HTMLResponse:
        warn = ('<p class="tag err">帳號或密碼不對</p>' if err else "")
        return HTMLResponse(f"""<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登入 — suno-web</title><style>{_CSS}</style></head><body>
<main class="login"><div class="card"><h2>suno-web 管理台</h2>{warn}
<form method="post" action="{url('/admin/login')}">
<p><input type="text" name="username" placeholder="帳號" autofocus></p>
<p><input type="password" name="password" placeholder="密碼"></p>
<p class="row"><label class="row"><input type="checkbox" name="remember"> 記住我 30 天</label></p>
<p><button type="submit">登入</button></p></form></div></main></body></html>""")

    @router.post("/admin/login")
    async def login(username: str = Form(""), password: str = Form(""),
                    remember: str = Form("")):
        ok = (constant_equals(username, settings.admin_username)
              and constant_equals(password, settings.admin_password))
        if not ok:
            return RedirectResponse(url("/admin/login?err=1"), status_code=303)
        ttl = 30 * 86400 if remember else 86400
        token = create_admin_session(username, settings.admin_session_secret,
                                     ttl_seconds=ttl)
        resp = RedirectResponse(url("/admin"), status_code=303)
        resp.set_cookie(_COOKIE, token, httponly=True, samesite="lax",
                        max_age=ttl, path="/")
        return resp

    @router.get("/admin/logout")
    async def logout():
        resp = RedirectResponse(url("/admin/login"), status_code=303)
        resp.delete_cookie(_COOKIE, path="/")
        return resp

    # ---- 總覽 ----

    @router.get("/admin", response_class=HTMLResponse)
    async def overview(request: Request):
        if not _current_user(request):
            return _login_redirect()
        info = health_extra() if health_extra else {}
        jobs = store.list_recent(200)
        done = sum(1 for j in jobs if j.status == "done")
        failed = sum(1 for j in jobs if j.status == "error")
        alive = info.get("browser_alive")
        credits = info.get("credits")
        stats = f"""<div class="grid">
<div class="stat"><b><span class="{'tag done' if alive else 'tag err'}">{'正常' if alive else '沒起來'}</span></b><small>瀏覽器</small></div>
<div class="stat"><b>{queue.queue_size}</b><small>排隊中</small></div>
<div class="stat"><b>{_esc(credits) if credits is not None else '未知'}</b><small>剩餘點數</small></div>
<div class="stat"><b>{int((time.time() - started_at) // 60)} 分</b><small>服務已執行</small></div>
<div class="stat"><b>{done}</b><small>近 200 筆成功</small></div>
<div class="stat"><b>{failed}</b><small>近 200 筆失敗</small></div>
</div>"""
        keys = admin_db.list_api_keys()
        static_n = len(settings.api_keys)
        note = (f'<p class="muted">動態金鑰 {len(keys)} 把、.env 靜態金鑰 '
                f'{static_n} 把。生成一單約 2 到 4 分鐘，一單扣 10 點。</p>')
        return _page("總覽", "overview",
                     f'<div class="card"><h2>服務狀態</h2>{stats}{note}</div>')

    # ---- 金鑰 ----

    @router.get("/admin/keys", response_class=HTMLResponse)
    async def keys_page(request: Request, new: str = ""):
        if not _current_user(request):
            return _login_redirect()
        banner = ""
        if new:
            banner = (f'<div class="new-key"><b>新金鑰（只會顯示這一次，關掉就看不到了）</b>'
                      f'<p><code>{_esc(new)}</code></p></div>')
        rows = []
        for k in admin_db.list_api_keys():
            state = ('<span class="tag done">啟用</span>' if k["enabled"]
                     else '<span class="tag err">停用</span>')
            toggle = "disable" if k["enabled"] else "enable"
            toggle_label = "停用" if k["enabled"] else "啟用"
            rows.append(f"""<tr><td>{_esc(k['name'])}</td><td><code>{_esc(k['id'])}</code></td>
<td>{state}</td><td>{_esc(k['requests_count'])}</td>
<td class="muted">{_esc(_fmt_iso(k['last_used_at']) if k['last_used_at'] else '未用過')}</td>
<td class="muted">{_esc(_fmt_iso(k['created_at']))}</td>
<td>
<form class="inline" method="post" action="{url(f"/admin/keys/{k['id']}/{toggle}")}"><button class="ghost">{toggle_label}</button></form>
<form class="inline" method="post" action="{url(f"/admin/keys/{k['id']}/delete")}" onsubmit="return confirm('刪掉就救不回來,確定?')"><button class="ghost">刪除</button></form>
</td></tr>""")
        table = ("".join(rows) or
                 '<tr><td colspan="7" class="muted">還沒發過金鑰</td></tr>')
        static_rows = "".join(
            f'<tr><td class="muted">.env 靜態金鑰</td><td><code>{_esc(k[:8])}…{_esc(k[-4:])}</code></td>'
            f'<td><span class="tag done">啟用</span></td><td colspan="4" class="muted">'
            f'改 .env 再重啟服務才會變</td></tr>'
            for k in sorted(settings.api_keys))
        return _page("金鑰", "keys", f"""{banner}
<div class="card"><h2>發一把新金鑰</h2>
<form method="post" action="{url('/admin/keys')}">
<input type="text" name="name" placeholder="用途，例如 筆電 CLI">
<button type="submit">產生</button></form></div>
<div class="card"><h2>金鑰清單</h2><table>
<tr><th>用途</th><th>ID</th><th>狀態</th><th>用過幾次</th><th>最後使用</th><th>建立時間</th><th></th></tr>
{table}{static_rows}</table></div>""")

    @router.post("/admin/keys")
    async def create_key(request: Request, name: str = Form("")):
        if not _current_user(request):
            return _login_redirect()
        _row, raw = admin_db.create_api_key(name)
        return RedirectResponse(url(f"/admin/keys?new={raw}"), status_code=303)

    @router.post("/admin/keys/{key_id}/{action}")
    async def key_action(request: Request, key_id: str, action: str):
        if not _current_user(request):
            return _login_redirect()
        if action == "delete":
            admin_db.delete_api_key(key_id)
        elif action in ("enable", "disable"):
            admin_db.set_api_key_enabled(key_id, action == "enable")
        return RedirectResponse(url("/admin/keys"), status_code=303)

    # ---- 歷史 ----

    @router.get("/admin/history", response_class=HTMLResponse)
    async def history(request: Request):
        if not _current_user(request):
            return _login_redirect()
        rows = []
        for job in store.list_recent(200):
            rows.append(_history_row(job, url))
        table = ("".join(rows) or
                 '<tr><td colspan="6" class="muted">還沒有任何 job</td></tr>')
        return _page("歷史", "history", f"""<div class="card"><h2>近 200 筆 job</h2>
<table><tr><th>時間</th><th>來源金鑰</th><th>內容</th><th>狀態</th><th>耗時</th><th>產出</th></tr>
{table}</table></div>""")

    return router


def _history_row(job: Job, url) -> str:
    p = job.params or {}
    if p.get("mode") == "custom":
        desc = " ".join(x for x in (
            f"曲風:{p.get('style')}" if p.get("style") else "",
            f"歌名:{p.get('title')}" if p.get("title") else "",
            "有歌詞" if p.get("lyrics") else "") if x) or "Custom"
    else:
        desc = p.get("prompt") or "-"
    if p.get("instrumental"):
        desc += "（純音樂）"
    cls = {"done": "done", "error": "err"}.get(job.status, "run")
    state = f'<span class="tag {cls}">{_esc(job.status)}</span>'
    if job.error:
        state += f'<div class="muted">{_esc(job.error)}</div>'
        if job.error_message:
            state += f'<div class="muted">{_esc(job.error_message[:80])}</div>'
    elapsed = "-"
    if job.started_at and job.finished_at:
        elapsed = f"{job.finished_at - job.started_at:.0f} 秒"
    clips = []
    for c in job.clips:
        dur = f"{c.duration:.0f}s" if c.duration else "-"
        if c.filename:
            link = url(f"/api/jobs/{job.id}/files/{c.filename}")
            clips.append(f'<div><a class="link" href="{link}">{_esc(c.title or c.id[:8])}</a>'
                         f' <span class="muted">{dur}</span></div>')
        else:
            clips.append(f'<div class="muted">{_esc(c.title or c.id[:8])} {dur}'
                         f'（沒抓到音檔）</div>')
    return f"""<tr><td class="muted">{_fmt_time(job.created_at)}</td>
<td class="muted">{_esc(p.get('api_key_name') or '-')}</td>
<td>{_esc(desc[:70])}</td><td>{state}</td><td class="muted">{elapsed}</td>
<td>{''.join(clips) or '<span class="muted">-</span>'}</td></tr>"""
