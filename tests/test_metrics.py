"""Test metrics provider và giới hạn kích thước cache."""

from __future__ import annotations

import time

from web_search_mcp import cache

# --------------------------------------------------------------------------
# record() + stats()
# --------------------------------------------------------------------------


def test_ghi_va_tong_hop_theo_provider():
    cache.record("free", ok=True, ms=120.0)
    cache.record("free", ok=True, ms=180.0)
    cache.record("free", ok=False, ms=50.0, note="timeout")

    s = cache.stats(24)

    assert s["free"]["calls"] == 3
    assert s["free"]["ok"] == 2
    assert s["free"]["fail"] == 1
    assert abs(s["free"]["ok_rate"] - 2 / 3) < 0.01


def test_tach_rieng_tung_provider():
    cache.record("serper", ok=True, ms=300.0)
    cache.record("free", ok=True, ms=2000.0)

    s = cache.stats(24)

    assert set(s) == {"serper", "free"}
    assert s["serper"]["calls"] == 1
    assert s["free"]["calls"] == 1


def test_avg_va_max_latency():
    cache.record("free", ok=True, ms=100.0)
    cache.record("free", ok=True, ms=300.0)

    s = cache.stats(24)["free"]

    assert abs(s["avg_ms"] - 200.0) < 0.01
    assert abs(s["max_ms"] - 300.0) < 0.01


def test_stats_rong_khi_chua_ghi_gi():
    assert cache.stats(24) == {}


def test_ok_rate_bang_khong_khi_that_bai_het():
    cache.record("free", ok=False, ms=10.0, note="rate limit")
    cache.record("free", ok=False, ms=12.0, note="rate limit")

    assert cache.stats(24)["free"]["ok_rate"] == 0.0


def test_chi_tinh_trong_khoang_thoi_gian_yeu_cau():
    """Bản ghi cũ hơn cửa sổ N giờ không được tính."""
    conn = cache._connect()
    old_ts = time.time() - 48 * 3600
    conn.execute(
        "INSERT INTO stats (ts, provider, ok, ms, note) VALUES (?, ?, ?, ?, ?)",
        (old_ts, "free", 1, 100.0, ""),
    )
    conn.commit()

    cache.record("free", ok=True, ms=200.0)  # mới

    assert cache.stats(hours=24)["free"]["calls"] == 1, "chỉ bản ghi mới"
    assert cache.stats(hours=72)["free"]["calls"] == 2, "cả hai khi mở rộng cửa sổ"


# --------------------------------------------------------------------------
# recent_failures()
# --------------------------------------------------------------------------


def test_chi_tra_ve_lan_that_bai():
    cache.record("free", ok=True, ms=100.0, note="")
    cache.record("free", ok=False, ms=50.0, note="engine bị chặn")

    fails = cache.recent_failures(10)

    assert len(fails) == 1
    assert fails[0][1] == "free"
    assert fails[0][2] == "engine bị chặn"


def test_that_bai_moi_nhat_len_dau():
    cache.record("free", ok=False, ms=10.0, note="loi-1")
    cache.record("serper", ok=False, ms=20.0, note="loi-2")

    fails = cache.recent_failures(10)

    assert fails[0][2] == "loi-2", "mới nhất phải ở đầu"


def test_ton_trong_gioi_han_limit():
    for i in range(10):
        cache.record("free", ok=False, ms=10.0, note=f"loi-{i}")

    assert len(cache.recent_failures(3)) == 3


# --------------------------------------------------------------------------
# Giới hạn kích thước — fix #9
# --------------------------------------------------------------------------


def test_bang_stats_khong_phinh_vo_han(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_STATS_MAX_ROWS", "5")

    for i in range(20):
        cache.record("free", ok=True, ms=float(i))

    (count,) = cache._connect().execute("SELECT COUNT(*) FROM stats").fetchone()
    assert count <= 5, f"phải bị cắt còn <=5, thực tế {count}"


def test_cat_bo_ban_ghi_cu_nhat_truoc(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_STATS_MAX_ROWS", "3")

    for i in range(6):
        cache.record("free", ok=True, ms=float(i), note=f"n{i}")

    notes = [
        r[0]
        for r in cache._connect()
        .execute("SELECT note FROM stats ORDER BY id ASC")
        .fetchall()
    ]
    assert "n0" not in notes, "bản ghi cũ nhất phải bị xoá"
    assert "n5" in notes, "bản ghi mới nhất phải còn"


def test_bang_entries_khong_phinh_vo_han(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_CACHE_MAX_ENTRIES", "5")

    for i in range(20):
        cache.set(f"key-{i}", {"v": i}, ttl=3600)

    (count,) = cache._connect().execute("SELECT COUNT(*) FROM entries").fetchone()
    assert count <= 5, f"phải bị cắt còn <=5, thực tế {count}"


def test_nguong_doc_lai_moi_lan_goi(monkeypatch):
    """Đọc env lazily — không cần reload module khi test đổi ngưỡng."""
    monkeypatch.setenv("WEB_SEARCH_STATS_MAX_ROWS", "7")
    assert cache.max_stats() == 7

    monkeypatch.setenv("WEB_SEARCH_STATS_MAX_ROWS", "9")
    assert cache.max_stats() == 9, "phải đọc lại, không cache giá trị cũ"


def test_nguong_mac_dinh_khi_khong_dat_env(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_STATS_MAX_ROWS", raising=False)
    monkeypatch.delenv("WEB_SEARCH_CACHE_MAX_ENTRIES", raising=False)

    assert cache.max_stats() > 0
    assert cache.max_entries() > 0


# --------------------------------------------------------------------------
# Cách ly cache giữa các test
# --------------------------------------------------------------------------


def test_moi_test_co_db_rieng():
    """Fixture isolated_cache phải đảm bảo test này không thấy dữ liệu test khác."""
    assert cache.stats(24) == {}
    cache.record("free", ok=True, ms=1.0)
    assert cache.stats(24)["free"]["calls"] == 1
