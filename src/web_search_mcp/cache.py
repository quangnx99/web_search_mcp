"""Cache SQLite với TTL, kèm bảng metrics theo dõi provider.

Hai bảng:
  - entries: cache kết quả search/fetch, có TTL
  - stats:   log mỗi lần gọi provider (thành công/thất bại, độ trễ)
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import platformdirs

_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None


def _db_path() -> Path:
    """Đường dẫn cache.

    Dùng thư mục cache theo chuẩn OS (LOCALAPPDATA trên Windows, XDG trên
    Linux/macOS) thay vì cạnh source. Khi cài qua uvx/pipx, source nằm trong
    môi trường tạm và bị xoá sau mỗi lần chạy — cache đặt ở đó sẽ mất sạch.
    """
    if env := os.getenv("WEB_SEARCH_CACHE_DB"):
        return Path(env)
    return Path(platformdirs.user_cache_dir("web-search-mcp")) / "cache.db"


def db_path() -> Path:
    """Đường dẫn file cache đang dùng (public để CLI hiển thị)."""
    return _db_path()


def max_entries() -> int:
    """Số entry cache tối đa. Đọc env mỗi lần gọi để test không cần reload module."""
    return max(1, int(os.getenv("WEB_SEARCH_CACHE_MAX_ENTRIES", "500")))


def max_stats() -> int:
    """Số dòng metrics tối đa giữ lại."""
    return max(1, int(os.getenv("WEB_SEARCH_STATS_MAX_ROWS", "1000")))


def _connect() -> sqlite3.Connection:
    """Kết nối lười, mở lại nếu đường dẫn thay đổi."""
    global _conn, _conn_path

    path = _db_path()
    if _conn is not None and _conn_path == path:
        return _conn

    if _conn is not None:
        _conn.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS entries "
        "(key TEXT PRIMARY KEY, value TEXT, expires_at REAL)"
    )
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS stats "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, provider TEXT, "
        "ok INTEGER, ms REAL, note TEXT)"
    )
    _conn.commit()
    _conn_path = path
    return _conn


def get(key: str):
    row = (
        _connect()
        .execute(
            "SELECT value FROM entries WHERE key = ? AND expires_at > ?",
            (key, time.time()),
        )
        .fetchone()
    )
    return json.loads(row[0]) if row else None


def set(key: str, value, ttl: int) -> None:
    conn = _connect()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO entries VALUES (?, ?, ?)",
        (key, json.dumps(value), now + ttl),
    )
    conn.execute("DELETE FROM entries WHERE expires_at <= ?", (now,))

    # Chặn phình vô hạn: bỏ entry hết hạn sớm nhất khi vượt ngưỡng.
    limit = max_entries()
    (count,) = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
    if count > limit:
        conn.execute(
            "DELETE FROM entries WHERE key IN "
            "(SELECT key FROM entries ORDER BY expires_at ASC LIMIT ?)",
            (count - limit,),
        )
    conn.commit()


def record(provider: str, ok: bool, ms: float, note: str = "") -> None:
    """Ghi một lần gọi provider vào metrics.

    Không bọc try/except: lỗi ở đây nghĩa là schema sai, và im lặng sẽ khiến
    bug loại đó cực khó chẩn đoán.
    """
    conn = _connect()
    conn.execute(
        "INSERT INTO stats (ts, provider, ok, ms, note) VALUES (?, ?, ?, ?, ?)",
        (time.time(), provider, 1 if ok else 0, ms, note),
    )

    limit = max_stats()
    (count,) = conn.execute("SELECT COUNT(*) FROM stats").fetchone()
    if count > limit:
        conn.execute(
            "DELETE FROM stats WHERE id IN "
            "(SELECT id FROM stats ORDER BY id ASC LIMIT ?)",
            (count - limit,),
        )
    conn.commit()


def stats(hours: int = 24) -> dict[str, dict]:
    """Tổng hợp metrics theo provider trong N giờ gần nhất."""
    since = time.time() - hours * 3600
    rows = _connect().execute(
        "SELECT provider, COUNT(*), SUM(ok), AVG(ms), MAX(ms) "
        "FROM stats WHERE ts >= ? GROUP BY provider",
        (since,),
    ).fetchall()

    out: dict[str, dict] = {}
    for provider, total, oks, avg_ms, max_ms in rows:
        oks = oks or 0
        out[provider] = {
            "calls": total,
            "ok": oks,
            "fail": total - oks,
            "ok_rate": oks / total if total else 0.0,
            "avg_ms": avg_ms or 0.0,
            "max_ms": max_ms or 0.0,
        }
    return out


def recent_failures(limit: int = 5) -> list[tuple[float, str, str]]:
    """Các lần thất bại gần nhất: (timestamp, provider, lý do)."""
    return _connect().execute(
        "SELECT ts, provider, note FROM stats WHERE ok = 0 ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
