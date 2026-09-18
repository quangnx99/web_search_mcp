"""Test CLI: dispatch lệnh con và exit code. Không gọi mạng."""

from __future__ import annotations

import pytest

from web_search_mcp import cli, server


@pytest.fixture
def argv(monkeypatch):
    """Đặt sys.argv cho một lần gọi cli.main() (bỏ qua tên chương trình)."""

    def _set(*args: str) -> None:
        monkeypatch.setattr("sys.argv", ["web-search-mcp", *args])

    return _set


@pytest.fixture
def stub_server(monkeypatch):
    """Chặn server.main() để test không thực sự chạy MCP loop (blocking)."""
    calls: list[bool] = []
    monkeypatch.setattr(server, "main", lambda: calls.append(True))
    return calls


# --------------------------------------------------------------------------
# Đường chạy server — phải giữ nguyên để MCP client không bị ảnh hưởng
# --------------------------------------------------------------------------


def test_khong_tham_so_thi_chay_mcp_server(argv, stub_server):
    argv()
    assert cli.main() == 0
    assert stub_server == [True]


def test_la_lac_o_vi_tri_dau_van_chay_mcp_server(argv, stub_server):
    """Một số MCP client truyền cờ riêng; không được vì thế mà báo lỗi."""
    argv("--transport", "stdio")
    assert cli.main() == 0
    assert stub_server == [True]


def test_lenh_serve_tuong_minh(argv, stub_server):
    argv("serve")
    assert cli.main() == 0
    assert stub_server == [True]


# --------------------------------------------------------------------------
# Lệnh con
# --------------------------------------------------------------------------


def test_search_thanh_cong_tra_exit_0(argv, monkeypatch, capsys):
    async def fake(query, count=5, lang="auto"):
        return f"Nguồn: free — 1 kết quả ({query}/{count}/{lang})"

    monkeypatch.setattr(server, "web_search", fake)
    argv("search", "giá vàng", "--count", "3", "--lang", "vi")

    assert cli.main() == 0
    assert "giá vàng/3/vi" in capsys.readouterr().out


def test_search_that_bai_tra_exit_1(argv, monkeypatch, capsys):
    """Script hoá cần biết search hỏng, không chỉ đọc chuỗi."""

    async def fake(query, count=5, lang="auto"):
        return "Tìm kiếm thất bại với mọi provider:\n  - free: bị chặn"

    monkeypatch.setattr(server, "web_search", fake)
    argv("search", "abc")

    assert cli.main() == 1
    assert "thất bại" in capsys.readouterr().out


def test_fetch_that_bai_tra_exit_1(argv, monkeypatch):
    async def fake(url, max_chars=20000):
        return f"Không đọc được {url}."

    monkeypatch.setattr(server, "fetch_page", fake)
    argv("fetch", "https://vd.vn")

    assert cli.main() == 1


def test_stats_in_ra_bang(argv, monkeypatch, capsys):
    async def fake(hours=24):
        return f"Thống kê {hours} giờ qua:"

    monkeypatch.setattr(server, "search_stats", fake)
    argv("stats", "--hours", "48")

    assert cli.main() == 0
    assert "48 giờ" in capsys.readouterr().out


def test_doctor_in_duong_dan_cache(argv, capsys):
    from web_search_mcp import cache

    argv("doctor")
    assert cli.main() == 0
    assert str(cache.db_path()) in capsys.readouterr().out


def test_version_thoat_voi_exit_0(argv):
    argv("--version")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0