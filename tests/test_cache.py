"""Test cache SQLite: TTL, isolation, kiểu dữ liệu."""

from __future__ import annotations

import time

from web_search_mcp import cache


def test_get_tra_none_khi_chua_co_key():
    assert cache.get("khong-ton-tai") is None


def test_set_roi_get_tra_dung_gia_tri():
    cache.set("k1", {"provider": "free", "results": [{"title": "a"}]}, 60)
    assert cache.get("k1") == {"provider": "free", "results": [{"title": "a"}]}


def test_luu_duoc_chuoi_thuan():
    cache.set("k-str", "nội dung markdown tiếng Việt", 60)
    assert cache.get("k-str") == "nội dung markdown tiếng Việt"


def test_ttl_het_han_thi_coi_nhu_khong_co():
    cache.set("k-expired", "cũ", -1)  # đã hết hạn ngay khi ghi
    assert cache.get("k-expired") is None


def test_ttl_con_hieu_luc_thi_van_lay_duoc():
    cache.set("k-alive", "mới", 300)
    assert cache.get("k-alive") == "mới"


def test_set_lai_cung_key_thi_ghi_de():
    cache.set("k-dup", "lần 1", 60)
    cache.set("k-dup", "lần 2", 60)
    assert cache.get("k-dup") == "lần 2"


def test_set_don_dep_ban_ghi_het_han():
    cache.set("rác", "x", -1)
    cache.set("mới", "y", 60)  # lần set này kích hoạt dọn dẹp

    conn = cache._connect()
    keys = {r[0] for r in conn.execute("SELECT key FROM entries")}
    assert "rác" not in keys
    assert "mới" in keys


def test_db_path_doc_tu_env(tmp_path, monkeypatch):
    riêng = tmp_path / "khac.db"
    monkeypatch.setenv("WEB_SEARCH_CACHE_DB", str(riêng))
    cache._conn = None  # buộc mở lại kết nối

    cache.set("k", "v", 60)
    assert riêng.exists()
    assert cache.get("k") == "v"


def test_ttl_thuc_te_het_han_sau_thoi_gian_ngan():
    cache.set("k-short", "v", 1)
    assert cache.get("k-short") == "v"
    time.sleep(1.1)
    assert cache.get("k-short") is None
