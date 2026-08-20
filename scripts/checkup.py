#!/usr/bin/env python3
"""一次看完服務狀態、近期成功率、失敗明細、點數與音檔佔用。

唯讀、只用標準函式庫，沒事的時候跑一下看服務有沒有在偷偷爛掉。

    python3 scripts/checkup.py
    python3 scripts/checkup.py --server https://ching-tech.ddns.net/suno-web
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path.home() / ".suno-web"


def http_json(url: str, timeout: int = 10) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def dir_size(path: Path) -> tuple[int, int]:
    total, files = 0, 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += (Path(root) / n).stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def jobs_report(db: Path, hours: int) -> None:
    if not db.is_file():
        print(f"  找不到 {db}，這台可能沒跑過 job")
        return
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT status, error, error_message, params, created_at,"
        " started_at, finished_at FROM jobs WHERE created_at >= ?"
        " ORDER BY created_at DESC", (cutoff,)).fetchall()
    total_rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    done = [r for r in rows if r[0] == "done"]
    failed = [r for r in rows if r[0] == "error"]
    stuck = [r for r in rows if r[0] in ("queued", "generating")]
    rate = f"{len(done)}/{len(done) + len(failed)}" if (done or failed) else "沒有資料"
    print(f"  近 {hours} 小時：成功 {rate}、卡住 {len(stuck)} 筆")
    print(f"  資料庫共 {total_rows} 筆（超過 1000 筆會自動裁切）")
    if done:
        spans = [r[6] - r[5] for r in done if r[5] and r[6]]
        if spans:
            print(f"  成功那幾單耗時：中位數 {sorted(spans)[len(spans) // 2]:.0f} 秒、"
                  f"最久 {max(spans):.0f} 秒")
    for r in failed[:5]:
        when = time.strftime("%m-%d %H:%M", time.localtime(r[4]))
        print(f"    失敗 {when}　{r[1]}：{(r[2] or '')[:60]}")
    for r in stuck[:3]:
        when = time.strftime("%m-%d %H:%M", time.localtime(r[4]))
        age = (time.time() - r[4]) / 60
        flag = "（超過 15 分鐘，八成是服務中途被砍掉留下的）" if age > 15 else ""
        print(f"    卡住 {when}　{r[0]} 已 {age:.0f} 分鐘{flag}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8071")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    print("== 服務 ==")
    health = http_json(f"{args.server.rstrip('/')}/api/health")
    if health is None:
        print(f"  打不到 {args.server}（服務沒起來，或位址不對）")
    else:
        alive = "活著" if health.get("browser_alive") else "沒起來"
        credits = health.get("credits")
        credit_txt = ("尚未觀測" if credits is None
                      else f"{credits}（還能生 {int(credits) // 10} 單）")
        print(f"  瀏覽器 {alive}、排隊 {health.get('queue_size')} 筆、"
              f"已執行 {health.get('uptime_seconds', 0) / 3600:.1f} 小時")
        print(f"  點數 {credit_txt}")

    print("== job ==")
    jobs_report(DATA / "jobs.db", args.hours)

    print("== 音檔 ==")
    gen = DATA / "generated"
    if gen.is_dir():
        size, files = dir_size(gen)
        dirs = sum(1 for c in gen.iterdir() if c.is_dir())
        print(f"  {dirs} 個 job 目錄、{files} 個檔案、共 {human_bytes(size)}")
    else:
        print(f"  還沒有 {gen}")

    print("== 登入態 ==")
    prof = DATA / "profiles"
    if prof.is_dir():
        size, _ = dir_size(prof)
        age = (time.time() - prof.stat().st_mtime) / 86400
        print(f"  profile 佔 {human_bytes(size)}、最後異動 {age:.1f} 天前")
        print("  登入是不是還有效要看 canary：python3 scripts/canary.py --dry-run")
    else:
        print(f"  還沒登入過（{prof} 不存在）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
