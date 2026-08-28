"""Admin webui：總覽、金鑰、測試、歷史。

版面沿用 gemini-web 與 codex-image-service 那一家的結構：頂部列、左側邊欄、
頁面大標題配一句說明、底下才是卡片。配色刻意不同——深色底配珊瑚橘與洋紅，
免得兩個管理台長得太像會搞錯（見 AGENTS.md）。

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
from .jobs import (Job, JobQueue, JobStore, QueueFullError,
                   cleanup_expired)
from .security import (constant_equals, create_admin_session,
                       verify_admin_session)

_COOKIE = "suno_admin"

_NAV = (
    ("overview", "/admin", "Tổng quan", "◎"),
    ("keys", "/admin/keys", "Thẻ API Key", "⌘"),
    ("test", "/admin/test", "Thử nghiệm", "▶"),
    ("history", "/admin/history", "Lịch sử", "☰"),
)

_CSS = """
:root{
  color-scheme:dark;
  --bg:#151110; --panel:#1f1a18; --panel2:#191413; --line:#2f2724; --ink:#f4ece7;
  --muted:#a89489; --accent:#ff7a59; --accent2:#ff4d9d;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 system-ui,"Noto Sans TC",sans-serif}
a{color:inherit}
.topbar{display:flex;align-items:center;gap:12px;padding:14px 22px;
  border-bottom:1px solid var(--line);background:#1a1513}
.logo{width:30px;height:30px;border-radius:99px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--accent),var(--accent2));color:#16110f;
  font-weight:700}
.brand{font-size:17px;font-weight:700;letter-spacing:.2px}
.topbar .right{margin-left:auto;display:flex;align-items:center;gap:10px}
.chip{background:#241d1b;border:1px solid var(--line);border-radius:999px;
  padding:5px 14px;font-size:13px;color:var(--muted)}
.btn-out{border:1px solid var(--line);border-radius:999px;padding:6px 16px;
  text-decoration:none;font-size:14px;color:var(--ink)}
.btn-out:hover{border-color:var(--accent)}
.shell{display:flex;align-items:stretch;min-height:calc(100vh - 60px)}
aside{width:220px;flex:none;border-right:1px solid var(--line);padding:16px 10px}
aside a{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:11px;
  text-decoration:none;color:var(--muted);margin-bottom:4px;font-size:15px}
aside a .ico{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;
  background:#241d1b;font-size:13px}
aside a:hover{color:var(--ink);background:#221b19}
aside a.on{color:var(--ink);background:#2a201d;font-weight:600;
  box-shadow:inset 3px 0 0 var(--accent)}
aside a.on .ico{background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#16110f}
main{flex:1;padding:28px 32px;max-width:1120px}
h1{margin:0 0 6px;font-size:27px;letter-spacing:-.3px}
.sub{margin:0 0 22px;color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:15px;
  padding:20px 22px;margin-bottom:18px}
.card h2{margin:0 0 4px;font-size:17px}
.card .hint{margin:0 0 16px;color:var(--muted);font-size:14px}
.card h2:last-child{margin-bottom:0}
.stats{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  margin-bottom:18px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:15px;
  padding:16px 18px}
.stat b{display:block;font-size:26px;line-height:1.25}
.stat small{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th{color:var(--muted);font-weight:600;font-size:11.5px;letter-spacing:.7px;
  text-transform:uppercase;text-align:left;padding:0 10px 10px}
td{padding:12px 10px;border-top:1px solid var(--line);vertical-align:top}
code{background:#241d1b;padding:2px 7px;border-radius:6px;font-size:13px}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12.5px;
  border:1px solid var(--line)}
.pill.ok{color:var(--ok);border-color:#245c39;background:#16281d}
.pill.bad{color:var(--bad);border-color:#5c2424;background:#2a1818}
.pill.run{color:var(--warn);border-color:#5c4a24;background:#2a2318}
label.field{display:block;margin-bottom:14px}
label.field span{display:block;margin-bottom:6px;font-size:14px;color:var(--muted)}
input[type=text],input[type=password],input[type=number],textarea,select{
  width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);
  border-radius:11px;padding:11px 13px;font:inherit}
textarea{min-height:96px;resize:vertical}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(255,122,89,.18)}
input::placeholder,textarea::placeholder{color:#7d6a62}
/* Chrome 自動填入會用自己的白底深字蓋掉樣式,在深色卡片上很突兀。內陰影是
   唯一壓得住它背景的辦法,文字色要用 -webkit-text-fill-color。 */
input:-webkit-autofill,input:-webkit-autofill:hover,input:-webkit-autofill:focus{
  -webkit-text-fill-color:var(--ink);
  -webkit-box-shadow:0 0 0 1000px var(--panel2) inset;
  caret-color:var(--ink);transition:background-color 9999s ease-out 0s}
.check{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:14px;
  margin-bottom:14px}
.check input{width:auto}
button{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#16110f;
  border:0;border-radius:999px;padding:10px 22px;font:inherit;font-weight:600;
  cursor:pointer}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--ink);
  padding:7px 15px;font-weight:500}
button.danger{background:#c8384a;color:#fff;padding:7px 15px;font-weight:500}
form.inline{display:inline}
.rowgap{display:flex;gap:8px;flex-wrap:wrap}
.newkey{background:#1c2a22;border:1px solid #2f5c46;border-radius:13px;padding:16px;
  margin-bottom:18px;word-break:break-all}
.muted{color:var(--muted)}
.link{color:var(--accent)}
audio{width:260px;max-width:100%;margin-top:4px}
.login-wrap{max-width:360px;margin:12vh auto;padding:0 16px}
@media (max-width:760px){
  .shell{display:block}
  aside{width:auto;border-right:0;border-bottom:1px solid var(--line);
    display:flex;gap:6px;overflow-x:auto;padding:10px}
  aside a{margin:0;white-space:nowrap}
  main{padding:20px 16px}
}
"""


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt_epoch(ts: float | None) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def _fmt_iso(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%m-%d %H:%M")


def create_admin_router(*, settings: Settings, store: JobStore,
                        queue: JobQueue, started_at: float,
                        health_extra, submit=None) -> APIRouter:
    router = APIRouter()
    prefix = settings.admin_url_prefix

    def url(path: str) -> str:
        return f"{prefix}{path}"

    def _page(*, title: str, subtitle: str, active: str, body: str,
              user: str, refresh: int = 0) -> HTMLResponse:
        nav = "".join(
            f'<a class="{"on" if key == active else ""}" href="{url(href)}">'
            f'<span class="ico">{ico}</span>{label}</a>'
            for key, href, label, ico in _NAV)
        meta_refresh = (f'<meta http-equiv="refresh" content="{refresh}">'
                        if refresh else "")
        return HTMLResponse(f"""<!doctype html><html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{meta_refresh}<title>{_esc(title)} — suno-web</title><style>{_CSS}</style></head><body>
<div class="topbar"><div class="logo">S</div><div class="brand">Quản trị suno-web</div>
<div class="right"><span class="chip">{_esc(user)}</span>
<a class="btn-out" href="{url('/admin/logout')}">Đăng xuất</a></div></div>
<div class="shell"><aside>{nav}</aside>
<main><h1>{_esc(title)}</h1><p class="sub">{subtitle}</p>{body}</main></div>
</body></html>""")

    def _user(request: Request) -> str | None:
        return verify_admin_session(request.cookies.get(_COOKIE),
                                    settings.admin_session_secret)

    def _to_login() -> RedirectResponse:
        return RedirectResponse(url("/admin/login"), status_code=303)

    # ---- Đăng nhập ----

    @router.get("/admin/login", response_class=HTMLResponse)
    async def login_page(err: str = "") -> HTMLResponse:
        warn = ('<p><span class="pill bad">Tên đăng nhập hoặc mật khẩu không chính xác</span></p>' if err else "")
        return HTMLResponse(f"""<!doctype html><html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Đăng nhập — suno-web</title><style>{_CSS}</style></head><body>
<div class="login-wrap"><div class="card">
<div class="rowgap" style="align-items:center;margin-bottom:14px">
<div class="logo">S</div><div class="brand">Quản trị suno-web</div></div>{warn}
<form method="post" action="{url('/admin/login')}">
<label class="field"><span>Tên đăng nhập</span>
<input type="text" name="username" autofocus autocomplete="username"></label>
<label class="field"><span>Mật khẩu</span>
<input type="password" name="password" autocomplete="current-password"></label>
<label class="check"><input type="checkbox" name="remember"> Ghi nhớ đăng nhập 30 ngày</label>
<button type="submit">Đăng nhập</button></form></div></div></body></html>""")

    @router.post("/admin/login")
    async def login(username: str = Form(""), password: str = Form(""),
                    remember: str = Form("")):
        if not (constant_equals(username, settings.admin_username)
                and constant_equals(password, settings.admin_password)):
            return RedirectResponse(url("/admin/login?err=1"), status_code=303)
        ttl = 30 * 86400 if remember else 86400
        resp = RedirectResponse(url("/admin"), status_code=303)
        resp.set_cookie(_COOKIE,
                        create_admin_session(username,
                                             settings.admin_session_secret,
                                             ttl_seconds=ttl),
                        httponly=True, samesite="lax", max_age=ttl, path="/")
        return resp

    @router.get("/admin/logout")
    async def logout():
        resp = RedirectResponse(url("/admin/login"), status_code=303)
        resp.delete_cookie(_COOKIE, path="/")
        return resp

    # ---- 總覽 ----

    @router.get("/admin", response_class=HTMLResponse)
    async def overview(request: Request):
        user = _user(request)
        if not user:
            return _to_login()
        info = health_extra() if health_extra else {}
        jobs = store.list_recent(200)
        done = [j for j in jobs if j.status == "done"]
        failed = sum(1 for j in jobs if j.status == "error")
        running = [j for j in jobs if j.status in ("queued", "generating")]
        alive = bool(info.get("browser_alive"))
        credits = info.get("credits")

        # 點數是硬上限（實測免費方案 100 點／月、一單 10 點），用完就整個停擺，
        # 所以擺第一格，而且直接換算成「還能生幾單」——那才是要做決定時看的數字。
        if isinstance(credits, (int, float)):
            credit_cell = (f'<b>{int(credits)}</b>'
                           f'<small>Điểm còn lại (Tạo được ~{int(credits) // 10} bài)</small>')
        else:
            credit_cell = ('<b class="muted">Chưa cập nhật</b>'
                           '<small>Điểm còn lại (Cập nhật sau 1 bài)</small>')
        uptime_h = (time.time() - started_at) / 3600
        uptime = f"{uptime_h:.1f}h" if uptime_h >= 1 else f"{int(uptime_h * 60)}m"
        stats = f"""<div class="stats">
<div class="stat">{credit_cell}</div>
<div class="stat"><b>{len(running)}</b><small>Đang xử lý / Hàng đợi</small></div>
<div class="stat"><b style="color:var(--ok)">{len(done)}</b><small>Thành công gần đây</small></div>
<div class="stat"><b style="color:var(--bad)">{failed}</b><small>Thất bại gần đây</small></div>
<div class="stat"><b>{uptime}</b><small>Thời gian hoạt động</small></div>
</div>"""

        now_card = ""
        if running:
            rows = "".join(
                f'<tr><td><span class="pill run">{_esc(j.status)}</span></td>'
                f'<td>{_esc(_describe(j.params)[:60])}</td>'
                f'<td class="muted">{_fmt_epoch(j.created_at)}</td>'
                f'<td class="muted">{_esc(j.id)}</td></tr>' for j in running)
            now_card = (f'<div class="card"><h2>Đang xử lý</h2>'
                        f'<p class="hint">Trang tự động cập nhật mỗi 15 giây. Mỗi bài mất khoảng 2-4 phút.</p>'
                        f'<table><tr><th>Trạng thái</th><th>Nội dung</th><th>Thời gian gửi</th>'
                        f'<th>Job ID</th></tr>{rows}</table></div>')

        latest = "".join(
            f'<div style="margin-bottom:14px"><div class="muted">'
            f'{_fmt_epoch(j.created_at)}・{_esc(_describe(j.params)[:50])}</div>'
            f'{_clips_html(j, url)}</div>' for j in done[:3])
        listen_card = (f'<div class="card"><h2>Bài hoàn thành gần đây</h2>'
                       f'<p class="hint"><a class="link" href="{url("/admin/history")}">'
                       f'Xem toàn bộ lịch sử →</a></p>{latest}</div>' if latest else "")

        workers = info.get("workers") or []
        worker_card = ""
        if len(workers) > 1:
            wrows = "".join(_worker_row(w) for w in workers)
            worker_card = (
                '<div class="card"><h2>Danh sách Tài khoản</h2>'
                '<p class="hint">Phân bổ luân phiên giữa các tài khoản. '
                'Trình duyệt tự động mở và ngủ khi rảnh để tiết kiệm bộ nhớ.</p>'
                '<table><tr><th>#</th><th>Trạng thái</th><th>Điểm còn lại</th>'
                '<th>Thành công</th><th>Thất bại</th><th>Dùng lần cuối</th></tr>'
                f'{wrows}</table></div>')

        keys = admin_db.list_api_keys()
        state_card = f"""<div class="card"><h2>Trạng thái Dịch vụ</h2><table>
<tr><th>Hạng mục</th><th>Trạng thái</th></tr>
<tr><td>Trình duyệt Chrome</td><td><span class="pill {'ok' if alive else 'bad'}">
{'Đang hoạt động' if alive else 'Chưa bật'}</span></td></tr>
<tr><td>Đăng nhập Suno</td><td><span class="pill {'ok' if info.get('logged_in') else 'run'}">
{'Đã đăng nhập' if info.get('logged_in') else 'Chưa cập nhật'}</span></td></tr>
<tr><td>Khóa API Key</td><td>Động {len(keys)} khóa, .env tĩnh {len(settings.api_keys)} khóa</td></tr>
</table></div>"""
        return _page(title="Tổng quan", active="overview", user=user,
                     refresh=15 if running else 0,
                     subtitle="Số điểm còn lại, tiến độ xử lý và bài hát vừa hoàn thành.",
                     body=stats + now_card + worker_card + listen_card + state_card)

    # ---- 金鑰 ----

    @router.get("/admin/keys", response_class=HTMLResponse)
    async def keys_page(request: Request, new: str = ""):
        user = _user(request)
        if not user:
            return _to_login()
        banner = (f'<div class="newkey"><b>Mã API Key mới (Chỉ hiển thị 1 lần này)</b>'
                  f'<p><code>{_esc(new)}</code></p></div>' if new else "")
        rows = []
        for k in admin_db.list_api_keys():
            toggle = "disable" if k["enabled"] else "enable"
            rows.append(f"""<tr>
<td><code>{_esc(k['id'])}</code></td><td><b>{_esc(k['name'])}</b></td>
<td><span class="pill {'ok' if k['enabled'] else 'bad'}">
{'Đang bật' if k['enabled'] else 'Đang tắt'}</span></td>
<td>{_esc(k['requests_count'])}</td>
<td class="muted">{_esc(_fmt_iso(k['last_used_at']) if k['last_used_at'] else 'Chưa dùng')}</td>
<td class="muted">{_esc(_fmt_iso(k['created_at']))}</td>
<td><div class="rowgap">
<form class="inline" method="post" action="{url(f"/admin/keys/{k['id']}/{toggle}")}">
<button class="ghost">{'Tắt' if k['enabled'] else 'Bật'}</button></form>
<form class="inline" method="post" action="{url(f"/admin/keys/{k['id']}/delete")}"
 onsubmit="return confirm('Xóa khóa API sẽ không thể phục hồi, bạn có chắc chắn?')">
<button class="danger">Xóa</button></form></div></td></tr>""")
        static_rows = "".join(
            f'<tr><td><code>{_esc(k[:8])}…{_esc(k[-4:])}</code></td>'
            f'<td class="muted">API Key tĩnh từ file .env</td>'
            f'<td><span class="pill ok">Đang bật</span></td>'
            f'<td colspan="4" class="muted">Thay đổi trong file .env và khởi động lại dịch vụ</td></tr>'
            for k in sorted(settings.api_keys))
        body = (rows and "".join(rows) or
                '<tr><td colspan="7" class="muted">Chưa có khóa API Key động nào được tạo</td></tr>')
        return _page(title="Thẻ API Key", active="keys", user=user,
                     subtitle="Tạo riêng khóa API Key cho từng ứng dụng gọi đến để phân biệt trong lịch sử.",
                     body=f"""{banner}
<div class="card"><h2>Tạo khóa API Key mới</h2>
<p class="hint">Nhập tên mô tả ứng dụng / thiết bị sử dụng khóa này.</p>
<form method="post" action="{url('/admin/keys')}">
<label class="field"><span>Mục đích sử dụng</span>
<input type="text" name="name" placeholder="Ví dụ: Laptop CLI, App mobile"></label>
<button type="submit">Tạo API Key</button></form></div>
<div class="card"><h2>Danh sách khóa API Key</h2>
<p class="hint">Cột ID là mã quản trị, ứng dụng gọi API phải dùng mã khóa gốc hiển thị lúc tạo.</p>
<table><tr><th>ID</th><th>Mục đích</th><th>Trạng thái</th><th>Lượt dùng</th><th>Lần cuối</th>
<th>Ngày tạo</th><th>Thao tác</th></tr>{body}{static_rows}</table></div>""")

    @router.post("/admin/keys")
    async def create_key(request: Request, name: str = Form("")):
        if not _user(request):
            return _to_login()
        _row, raw = admin_db.create_api_key(name)
        return RedirectResponse(url(f"/admin/keys?new={raw}"), status_code=303)

    @router.post("/admin/keys/{key_id}/{action}")
    async def key_action(request: Request, key_id: str, action: str):
        if not _user(request):
            return _to_login()
        if action == "delete":
            admin_db.delete_api_key(key_id)
        elif action in ("enable", "disable"):
            admin_db.set_api_key_enabled(key_id, action == "enable")
        return RedirectResponse(url("/admin/keys"), status_code=303)

    # ---- Thử nghiệm ----

    @router.get("/admin/test", response_class=HTMLResponse)
    async def test_page(request: Request, job: str = "", err: str = ""):
        user = _user(request)
        if not user:
            return _to_login()
        options = "".join(
            f'<option value="{_esc(k["name"])}">{_esc(k["name"])}</option>'
            for k in admin_db.list_api_keys() if k["enabled"])
        warn = (f'<p><span class="pill bad">{_esc(err)}</span></p>' if err else "")
        result, refresh = "", 0
        if job:
            result, refresh = _test_result(store.get(job), url)
        return _page(title="Thử nghiệm tạo nhạc", active="test", user=user, refresh=refresh,
                     subtitle="Gửi yêu cầu tạo nhạc thử nghiệm trực tiếp qua trang quản trị (Mỗi bài mất 10 điểm).",
                     body=f"""{warn}
<div class="card"><h2>Gửi yêu cầu tạo nhạc</h2>
<p class="hint">Điền Mô tả để dùng chế độ Simple; Điền Phong cách hoặc Lời bài hát để dùng chế độ Custom.</p>
<form method="post" action="{url('/admin/test')}">
<label class="field"><span>Mô tả bài hát (Chế độ Simple)</span>
<textarea name="prompt" placeholder="a warm ukulele tune about a sleepy cat"></textarea></label>
<label class="field"><span>Phong cách / Style (Chế độ Custom)</span>
<input type="text" name="style" placeholder="lo-fi hip hop"></label>
<label class="field"><span>Tiêu đề bài hát (Chế độ Custom)</span>
<input type="text" name="title" placeholder="Đêm khuya viết code"></label>
<label class="field"><span>Lời bài hát (Chế độ Custom)</span>
<textarea name="lyrics" placeholder="[Verse]&#10;Đêm đã về khuya..."></textarea></label>
<label class="check"><input type="checkbox" name="instrumental"> Nhạc không lời (Instrumental)</label>
<label class="field"><span>Gắn với thẻ API Key nào</span>
<select name="key_name"><option value="">Trang quản trị (Không gắn API Key)</option>{options}</select></label>
<button type="submit">Gửi yêu cầu và Tạo nhạc</button></form></div>{result}""")

    @router.post("/admin/test")
    async def run_test(request: Request, prompt: str = Form(""),
                       style: str = Form(""), title: str = Form(""),
                       lyrics: str = Form(""), instrumental: str = Form(""),
                       key_name: str = Form("")):
        if not _user(request):
            return _to_login()
        if submit is None:
            return RedirectResponse(url("/admin/test?err=Dịch vụ này chưa bật tính năng thử nghiệm tạo nhạc"),
                                    status_code=303)
        try:
            job_id = submit(prompt=prompt, lyrics=lyrics, style=style,
                            title=title, instrumental=bool(instrumental),
                            key_name=key_name or "Thử nghiệm Quản trị")
        except QueueFullError:
            return RedirectResponse(url("/admin/test?err=Hàng đợi đầy, vui lòng thử lại sau ít phút"),
                                    status_code=303)
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            return RedirectResponse(url(f"/admin/test?err={_esc(detail)[:120]}"),
                                    status_code=303)
        return RedirectResponse(url(f"/admin/test?job={job_id}"), status_code=303)

    # ---- Lịch sử ----

    @router.get("/admin/history", response_class=HTMLResponse)
    async def history(request: Request, swept: int | None = None):
        user = _user(request)
        if not user:
            return _to_login()
        rows = "".join(_history_row(j, url) for j in store.list_recent(200))
        note = (f'<p><span class="pill ok">Đã dọn dẹp {swept} thư mục bài hát đã hết hạn</span></p>'
                if swept is not None else "")
        return _page(title="Lịch sử", active="history", user=user,
                     subtitle="Danh sách 200 bài hát được yêu cầu gần nhất. Có thể nghe trực tiếp trên trình duyệt.",
                     body=f"""<div class="card"><h2>Các bài hát gần đây</h2><table>
<tr><th>Thời gian</th><th>API Key</th><th>Nội dung</th><th>Trạng thái</th><th>Thời gian xử lý</th><th>Kết quả nghe thử</th></tr>
{rows or '<tr><td colspan="6" class="muted">Chưa có bài hát nào được tạo</td></tr>'}
</table></div>
<div class="card"><h2>Dọn dẹp file âm thanh hết hạn</h2>
<p class="hint">File âm thanh được giữ trong {settings.audio_retention_days} ngày. Hệ thống sẽ tự động dọn dẹp file hết hạn.</p>
{note}<form method="post" action="{url('/admin/cleanup')}">
<button class="ghost">Dọn dẹp tệp âm thanh hết hạn ngay</button></form></div>""")

    @router.post("/admin/cleanup")
    async def cleanup_now(request: Request):
        if not _user(request):
            return _to_login()
        deleted = cleanup_expired(settings.generated_dir,
                                  settings.audio_retention_days)
        return RedirectResponse(url(f"/admin/history?swept={deleted}"),
                                status_code=303)

    return router


def _worker_row(w: dict) -> str:
    state = "Đang chạy" if w["busy"] else ("Đang chờ" if w["browser_up"] else "Đã ngủ")
    cls = "ok" if w["browser_up"] else "run"
    credits = w["credits"]
    if isinstance(credits, int):
        credit_txt = f"{credits} ({credits // 10} bài)"
    else:
        credit_txt = '<span class="muted">Chưa cập nhật</span>'
    return (f'<tr><td>{w["id"]}</td>'
            f'<td><span class="pill {cls}">{state}</span></td>'
            f'<td>{credit_txt}</td><td>{w["jobs_done"]}</td>'
            f'<td>{w["jobs_failed"]}</td>'
            f'<td class="muted">{_fmt_epoch(w["last_used"])}</td></tr>')


def _pill(status: str) -> str:
    return {"done": "ok", "error": "bad"}.get(status, "run")


def _describe(params: dict | None) -> str:
    p = params or {}
    if p.get("mode") == "custom":
        bits = [b for b in (f"Style:{p.get('style')}" if p.get("style") else "",
                            f"Tiêu đề:{p.get('title')}" if p.get("title") else "",
                            "Có lời" if p.get("lyrics") else "") if b]
        desc = " ".join(bits) or "Custom"
    else:
        desc = p.get("prompt") or "-"
    return desc + (" (Nhạc không lời)" if p.get("instrumental") else "")


def _clips_html(job: Job, url) -> str:
    out = []
    for c in job.clips:
        dur = f"{c.duration:.0f} giây" if c.duration else "-"
        name = _esc(c.title or c.id[:8])
        if c.filename:
            src = url(f"/api/jobs/{job.id}/files/{c.filename}")
            out.append(f'<div><b>{name}</b> <span class="muted">{dur}</span>'
                       f'<br><audio controls preload="metadata" src="{src}"></audio></div>')
        else:
            out.append(f'<div class="muted">{name} {dur} (Chưa tải được file)</div>')
    return "".join(out)


def _test_result(job: Job | None, url) -> tuple[str, int]:
    """Trả về (HTML, số giây tự động làm mới)."""
    if job is None:
        return "", 0
    running = job.status in ("queued", "generating")
    head = (f'<span class="pill {_pill(job.status)}">{_esc(job.status)}</span>'
            f' <span class="muted">Job ID: {_esc(job.id)}</span>')
    if running:
        body = ('<p class="hint">Đang tạo nhạc, trang sẽ tự động làm mới mỗi 5 giây. '
                'Quá trình mất 2-4 phút.</p>')
    elif job.status == "error":
        body = (f'<p class="hint">{_esc(job.error)}: '
                f'{_esc(job.error_message or "")}</p>')
    else:
        body = _clips_html(job, url)
    return f'<div class="card"><h2>Yêu cầu {_esc(job.id)} {head}</h2>{body}</div>', (5 if running else 0)


def _history_row(job: Job, url) -> str:
    state = f'<span class="pill {_pill(job.status)}">{_esc(job.status)}</span>'
    if job.error:
        state += f'<div class="muted">{_esc(job.error)}</div>'
        if job.error_message:
            state += f'<div class="muted">{_esc(job.error_message[:70])}</div>'
    tried = (job.params or {}).get("tried_workers")
    if tried:
        who = ", ".join(str(i) for i in tried)
        state += f'<div class="muted">Tài khoản {who} yêu cầu xác minh CAPTCHA, đã tự động chuyển tài khoản</div>'
    elapsed = "-"
    if job.started_at and job.finished_at:
        elapsed = f"{job.finished_at - job.started_at:.0f} giây"
    return f"""<tr><td class="muted">{_fmt_epoch(job.created_at)}</td>
<td class="muted">{_esc((job.params or {}).get('api_key_name') or '-')}</td>
<td>{_esc(_describe(job.params)[:70])}</td><td>{state}</td>
<td class="muted">{elapsed}</td>
<td>{_clips_html(job, url) or '<span class="muted">-</span>'}</td></tr>"""
