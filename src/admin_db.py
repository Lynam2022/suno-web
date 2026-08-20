"""動態 API 金鑰的儲存（~/.suno-web/admin.db）。

跟 job 記錄分開放：job 那張表由 worker 高頻寫入，金鑰這張表只有 admin 頁面
在動，各自一個檔比較不會互相卡鎖。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .security import generate_api_key, hash_api_key

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    requests_count INTEGER NOT NULL DEFAULT 0
)
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.admin_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(_SCHEMA)
        _conn.commit()
    return _conn


def init_db() -> None:
    with _lock:
        _connect()


def create_api_key(name: str) -> tuple[dict[str, Any], str]:
    """回 (資料列, 金鑰原文)。原文只有這一次拿得到，資料庫只存雜湊。"""
    raw = generate_api_key()
    key_id = f"key_{raw[-12:]}"
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO api_keys (id, name, key_hash, enabled, created_at)"
            " VALUES (?, ?, ?, 1, ?)",
            (key_id, name.strip() or "未命名", hash_api_key(raw), _now()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?",
                           (key_id,)).fetchone()
    return dict(row), raw


def list_api_keys() -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_api_key_by_token(token: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM api_keys WHERE key_hash = ?",
            (hash_api_key(token),)).fetchone()
    return dict(row) if row else None


def mark_api_key_used(key_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE api_keys SET last_used_at = ?,"
            " requests_count = requests_count + 1 WHERE id = ?",
            (_now(), key_id))
        conn.commit()


def set_api_key_enabled(key_id: str, enabled: bool) -> None:
    with _lock:
        conn = _connect()
        conn.execute("UPDATE api_keys SET enabled = ? WHERE id = ?",
                     (1 if enabled else 0, key_id))
        conn.commit()


def delete_api_key(key_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        conn.commit()


def has_any_dynamic_key() -> bool:
    with _lock:
        row = _connect().execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()
    return bool(row["n"])


def reset_for_tests() -> None:
    """測試用：換過 settings.admin_db_path 之後把連線重開。"""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
