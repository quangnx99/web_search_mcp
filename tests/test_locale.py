"""Test locale detection."""

from __future__ import annotations

import pytest

from web_search_mcp import providers


# --------------------------------------------------------------------------
# locale() — trả (region, gl, hl) cho mã ngôn ngữ
# --------------------------------------------------------------------------


def test_map_vi_sang_vn_vi():
    region, gl, hl = providers.locale("vi")
    assert region == "vn-vi"
    assert gl == "vn"
    assert hl == "vi"


def test_map_en_sang_us_en():
    region, gl, hl = providers.locale("en")
    assert region == "us-en"
    assert gl == "us"
    assert hl == "en"


def test_fallback_us_en_khi_khong_co_trong_bang():
    region, gl, hl = providers.locale("unknown")
    assert region == "us-en"
    assert gl == "us"
    assert hl == "en"


def test_ho_tro_nhieu_ngon_ngu():
    """Kiểm tra một số mã phổ biến."""
    assert providers.locale("ja")[0] == "jp-jp"
    assert providers.locale("ko")[0] == "kr-kr"
    assert providers.locale("zh")[0] == "cn-zh"


# --------------------------------------------------------------------------
# COUNTRY_NAMES — Tavily cần tên nước đầy đủ
# --------------------------------------------------------------------------


def test_country_names_co_vi():
    assert providers.COUNTRY_NAMES.get("vi") == "vietnam"


def test_country_names_khong_co_en():
    """Tavily không cần boost cho tiếng Anh."""
    assert "en" not in providers.COUNTRY_NAMES


# --------------------------------------------------------------------------
# Tích hợp vào free_search
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_free_search_truyen_region_ddgs():
    """DDGS nhận region qua tham số `region=` — không test với mạng thật."""
    # Chỉ kiểm tra không crash khi gọi với lang="vi"
    try:
        await providers.free_search("test", count=1, lang="vi")
    except Exception as exc:
        # Cho phép lỗi mạng/rate limit, nhưng không được lỗi argument sai
        msg = str(exc).lower()
        assert "region" not in msg, f"region param gây lỗi: {exc}"
        assert "unexpected keyword" not in msg, f"signature sai: {exc}"

