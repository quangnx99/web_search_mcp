"""Test 2 tool MCP: web_search, fetch_page. Mạng được mock hoàn toàn."""

from __future__ import annotations

import pytest

from web_search_mcp import providers, server

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------
# _normalize_cache_key + _dedupe_domains (fix #5)
# --------------------------------------------------------------------------


def test_normalize_cache_key():
    """Query viết khác nhau cho cùng cache entry."""
    assert server._normalize_cache_key("Docker  Compose!") == "docker compose"
    assert server._normalize_cache_key("  react   hooks?  ") == "react hooks"
    assert server._normalize_cache_key("PYTHON") == "python"
    assert server._normalize_cache_key("Giá vàng!") == "giá vàng"


def test_dedupe_domains_keeps_diverse():
    """Giới hạn mỗi domain 2 kết quả để tăng đa dạng."""
    rows = [
        {"title": "Doc1", "url": "https://docs.docker.com/a", "snippet": "A"},
        {"title": "Doc2", "url": "https://docs.docker.com/b", "snippet": "B"},
        {"title": "Doc3", "url": "https://www.docs.docker.com/c", "snippet": "C"},  # www. bị loại bỏ
        {"title": "M1", "url": "https://medium.com/x", "snippet": "X"},
        {"title": "M2", "url": "https://medium.com/y", "snippet": "Y"},
        {"title": "M3", "url": "https://medium.com/z", "snippet": "Z"},
        {"title": "Other", "url": "https://react.dev/", "snippet": "R"},
    ]
    kept = server._dedupe_domains(rows, max_per_domain=2)
    urls = [r["url"] for r in kept]
    # docs.docker.com: 2 đầu, bỏ thứ 3
    # medium.com: 2 đầu, bỏ thứ 3
    # react.dev: giữ
    assert urls == [
        "https://docs.docker.com/a",
        "https://docs.docker.com/b",
        "https://medium.com/x",
        "https://medium.com/y",
        "https://react.dev/",
    ]


def test_dedupe_domains_empty_url():
    """URL rỗng không gây lỗi."""
    rows = [{"title": "T1", "url": "", "snippet": "S1"}, {"title": "T2", "url": "https://a.com", "snippet": "S2"}]
    kept = server._dedupe_domains(rows, max_per_domain=1)
    assert len(kept) == 1
    assert kept[0]["url"] == "https://a.com"


# --------------------------------------------------------------------------
# _is_binary — bug PDF nhị phân (fix #2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,ten",
    [
        ("%PDF-1.5 %\xef 137 0 obj << /Filter /FlateDecode", "PDF"),
        ("PK\x03\x04\x14\x00\x06\x00", "ZIP/docx"),
        ("\x89PNG\r\n\x1a\n\x00\x00", "PNG"),
        ("GIF89a\x01\x00", "GIF"),
        ("\xff\xd8\xff\xe0\x00\x10JFIF", "JPEG"),
        ("%!PS-Adobe-3.0", "PostScript"),
        ("text\x00với\x00null", "có null byte"),
    ],
)
def test_phat_hien_nhi_phan(content, ten):
    assert server._is_binary(content), f"phải nhận ra {ten} là nhị phân"


@pytest.mark.parametrize(
    "content,ten",
    [
        ("# Tiêu đề\n\nĐoạn văn bản bình thường.", "markdown tiếng Việt"),
        ("# Heading\n\nPlain english text.", "markdown tiếng Anh"),
        ("", "chuỗi rỗng"),
        ("Giá vàng hôm nay: 4.262,86 USD/ounce — tăng 900.000 đồng/lượng.", "câu tiếng Việt đủ dấu"),
        ("日本語のテキスト", "tiếng Nhật"),
        ("a\tb\r\nc\n", "có tab/CRLF"),
    ],
)
def test_khong_bao_dong_gia_voi_van_ban(content, ten):
    """Quan trọng: tiếng Việt nhiều ký tự non-ASCII nhưng vẫn là printable.

    Nếu heuristic đếm sai, mọi trang tiếng Việt sẽ bị chặn oan.
    """
    assert not server._is_binary(content), f"{ten} không được coi là nhị phân"


def test_chi_kiem_tra_1kb_dau():
    """Nhị phân ở sâu trong file không nên làm chặn cả trang HTML hợp lệ."""
    content = "x" * 2000 + "\x00\x00\x00"
    assert not server._is_binary(content)


# --------------------------------------------------------------------------
# fetch_page
# --------------------------------------------------------------------------


@pytest.fixture
def jina(monkeypatch):
    """Điều khiển _fetch_jina: đặt .result hoặc .error."""

    class Ctl:
        result = "# Trang\n\nnội dung"
        error: Exception | None = None
        calls = 0

    async def fake(url):
        Ctl.calls += 1
        if Ctl.error:
            raise Ctl.error
        return Ctl.result

    monkeypatch.setattr(server, "_fetch_jina", fake)
    return Ctl


@pytest.fixture
def ddgs_fetch(monkeypatch):
    """Điều khiển _fetch_ddgs."""

    class Ctl:
        result = "# Từ ddgs"
        error: Exception | None = None
        calls = 0

    async def fake(url):
        Ctl.calls += 1
        if Ctl.error:
            raise Ctl.error
        return Ctl.result

    monkeypatch.setattr(server, "_fetch_ddgs", fake)
    return Ctl


async def test_fetch_dung_jina_khi_chay_tot(jina, ddgs_fetch):
    out = await server.fetch_page("https://vd.vn")

    assert out == "# Trang\n\nnội dung"
    assert ddgs_fetch.calls == 0, "jina OK thì không được gọi ddgs"


async def test_fetch_chuyen_sang_ddgs_khi_jina_loi(jina, ddgs_fetch):
    jina.error = RuntimeError("429 rate limit")

    out = await server.fetch_page("https://vd.vn")

    assert out == "# Từ ddgs"
    assert ddgs_fetch.calls == 1


async def test_fetch_chan_pdf_nhi_phan_tu_ddgs(jina, ddgs_fetch):
    """Trước fix: trả 2.4M ký tự rác '%PDF-1.5...' vào context Claude."""
    jina.error = RuntimeError("429 rate limit")
    ddgs_fetch.result = "%PDF-1.5 %\xef" + "\x00rác nhị phân" * 5000

    out = await server.fetch_page("https://arxiv.org/pdf/1706.03762")

    assert "tệp nhị phân" in out
    assert "%PDF" not in out, "không được để byte thô lọt vào context"
    assert len(out) < 500, f"thông báo lỗi phải ngắn, nhận được {len(out)} ký tự"


async def test_fetch_bao_loi_khi_ca_hai_that_bai(jina, ddgs_fetch):
    jina.error = RuntimeError("jina sập")
    ddgs_fetch.error = RuntimeError("ddgs sập")

    out = await server.fetch_page("https://vd.vn")

    assert "Không đọc được" in out
    assert "jina sập" in out and "ddgs sập" in out


async def test_fetch_khong_cache_ket_qua_nhi_phan(jina, ddgs_fetch):
    """Nội dung nhị phân bị chặn trước khi tới cache.set()."""
    jina.error = RuntimeError("429")
    ddgs_fetch.result = "%PDF-1.5 rác"
    url = "https://vd.vn/tai-lieu.pdf"

    await server.fetch_page(url)

    from web_search_mcp import cache

    assert cache.get(f"fetch:{url}") is None


async def test_fetch_dung_cache_o_lan_thu_hai(jina, ddgs_fetch):
    await server.fetch_page("https://vd.vn/cached")
    await server.fetch_page("https://vd.vn/cached")

    assert jina.calls == 1, "lần 2 phải lấy từ cache"


async def test_fetch_cat_bot_khi_qua_dai(jina, ddgs_fetch):
    jina.result = "x" * 5000

    out = await server.fetch_page("https://vd.vn/long", max_chars=100)

    assert "đã cắt bớt" in out
    assert "tổng 5000 ký tự" in out


# --------------------------------------------------------------------------
# web_search
# --------------------------------------------------------------------------


def _chain(monkeypatch, *providers_list):
    monkeypatch.setattr(providers, "CHAIN", list(providers_list))


def _ok(rows):
    async def fn(query, count, lang):
        return rows

    return fn


def _fail(exc):
    async def fn(query, count, lang):
        raise exc

    return fn


ROWS = [{"title": "Kết quả", "url": "https://vd.vn", "snippet": "mô tả"}]


async def test_search_tra_ket_qua_dinh_dang_dung(monkeypatch):
    _chain(monkeypatch, ("free", _ok(ROWS)))

    out = await server.web_search("truy vấn test", count=5)

    assert "Nguồn: free — 1 kết quả" in out
    assert "Kết quả" in out and "https://vd.vn" in out


async def test_search_bo_qua_provider_thieu_key(monkeypatch):
    """serper/tavily thiếu key phải để chain đi tiếp, không làm hỏng request."""
    _chain(
        monkeypatch,
        ("serper", _fail(providers.ProviderError("SERPER_API_KEY chưa được đặt"))),
        ("free", _ok(ROWS)),
    )

    out = await server.web_search("q abc")

    assert "Nguồn: free" in out


async def test_search_bo_qua_provider_loi_bat_ngo(monkeypatch):
    _chain(
        monkeypatch,
        ("serper", _fail(RuntimeError("500 server error"))),
        ("free", _ok(ROWS)),
    )

    out = await server.web_search("q xyz")

    assert "Nguồn: free" in out


async def test_search_bo_qua_provider_tra_rong(monkeypatch):
    _chain(monkeypatch, ("serper", _ok([])), ("free", _ok(ROWS)))

    out = await server.web_search("q rong")

    assert "Nguồn: free" in out


async def test_search_bao_loi_khi_moi_provider_that_bai(monkeypatch):
    _chain(
        monkeypatch,
        ("serper", _fail(providers.ProviderError("thiếu key"))),
        ("free", _fail(providers.ProviderError("bị chặn"))),
    )

    out = await server.web_search("q that bai")

    assert "Tìm kiếm thất bại" in out
    assert "thiếu key" in out and "bị chặn" in out


async def test_search_dung_cache_o_lan_thu_hai(monkeypatch):
    calls = {"n": 0}

    async def counting(query, count, lang):
        calls["n"] += 1
        return ROWS

    _chain(monkeypatch, ("free", counting))

    await server.web_search("truy vấn cache")
    out = await server.web_search("truy vấn cache")

    assert calls["n"] == 1
    assert "(cache)" in out


@pytest.mark.parametrize(
    "query,mong_doi",
    [
        ("giá vàng hôm nay", "vi"),
        ("cách cấu hình nginx", "vi"),
        ("rust async runtime", "en"),
        ("docker compose", "en"),
    ],
)
async def test_tu_phat_hien_ngon_ngu(monkeypatch, query, mong_doi):
    seen = {}

    async def spy(q, count, lang):
        seen["lang"] = lang
        return ROWS

    _chain(monkeypatch, ("free", spy))

    await server.web_search(query)
    assert seen["lang"] == mong_doi


async def test_lang_chi_dinh_tuong_minh_duoc_ton_trong(monkeypatch):
    seen = {}

    async def spy(q, count, lang):
        seen["lang"] = lang
        return ROWS

    _chain(monkeypatch, ("free", spy))

    await server.web_search("docker vietnam", lang="vi")
    assert seen["lang"] == "vi", "chỉ định tường minh phải thắng auto-detect"


@pytest.mark.parametrize("dau_vao,mong_doi", [(0, 1), (-5, 1), (100, 20), (7, 7)])
async def test_count_bi_gioi_han_1_den_20(monkeypatch, dau_vao, mong_doi):
    seen = {}

    async def spy(q, count, lang):
        seen["count"] = count
        return ROWS

    _chain(monkeypatch, ("free", spy))

    await server.web_search(f"q {dau_vao}", count=dau_vao)
    assert seen["count"] == mong_doi


async def test_cache_tach_rieng_theo_ngon_ngu(monkeypatch):
    calls = {"n": 0}

    async def counting(query, count, lang):
        calls["n"] += 1
        return ROWS

    _chain(monkeypatch, ("free", counting))

    await server.web_search("docker", lang="en")
    await server.web_search("docker", lang="vi")

    assert calls["n"] == 2, "khác lang phải là khác cache entry"


# --------------------------------------------------------------------------
# Đăng ký tool với MCP
# --------------------------------------------------------------------------


async def test_dung_ba_tool_duoc_expose():
    names = {t.name for t in await server.mcp.list_tools()}
    assert names == {"web_search", "fetch_page", "search_stats"}


async def test_tool_co_mo_ta_cho_llm():
    for tool in await server.mcp.list_tools():
        assert tool.description, f"{tool.name} thiếu description"
