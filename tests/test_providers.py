"""Test provider layer: validate backend, retry, chain fallback.

Toàn bộ test mock mạng để chạy deterministic — không phụ thuộc engine ngoài
đang sống hay bị rate limit.
"""

from __future__ import annotations

import pytest

from web_search_mcp import providers

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------
# _valid_backends — bug "bing" (fix #1)
# --------------------------------------------------------------------------


def test_bing_bi_loai_vi_khong_phai_engine():
    """'bing' là provider của duckduckgo/yahoo, không phải backend hợp lệ.

    Đây chính là bug làm ddgs âm thầm rơi về 'auto': người dùng tưởng đang
    dùng bing nhưng thực tế dùng auto.
    """
    assert "bing" not in providers._valid_backends("bing,brave,yandex")


def test_backend_hop_le_duoc_giu_dung_thu_tu():
    assert providers._valid_backends("yandex,brave") == "yandex,brave"


def test_auto_duoc_giu_nguyen():
    assert providers._valid_backends("auto") == "auto"
    assert providers._valid_backends("all") == "all"


def test_toan_bo_backend_sai_thi_ve_auto():
    assert providers._valid_backends("bing,khong-ton-tai") == "auto"


def test_backend_sai_lan_dung_thi_chi_giu_cai_dung():
    assert providers._valid_backends("bing,brave,bịa") == "brave"


def test_khoang_trang_duoc_cat_bo():
    assert providers._valid_backends(" brave , yandex ") == "brave,yandex"


def test_mac_dinh_la_auto():
    """Đo thực tế: auto 5/5 vs danh sách thủ công 0-3/5."""
    assert providers.FREE_BACKENDS == "auto"


# --------------------------------------------------------------------------
# free_search — retry (fix #3)
# --------------------------------------------------------------------------


class FakeDDGS:
    """Giả lập DDGS với chuỗi kết quả/exception định trước cho từng lần gọi."""

    scripted: list = []
    calls = 0

    def __init__(self, *a, **kw):
        pass

    def text(self, query, **kwargs):
        cls = type(self)
        idx = min(cls.calls, len(cls.scripted) - 1)
        cls.calls += 1
        outcome = cls.scripted[idx]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_ddgs(monkeypatch):
    import ddgs

    FakeDDGS.calls = 0
    FakeDDGS.scripted = []
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    return FakeDDGS


ROW = {"title": "Tiêu đề", "href": "https://vd.vn", "body": "đoạn mô tả"}


async def test_free_search_thanh_cong_ngay_lan_dau(fake_ddgs):
    fake_ddgs.scripted = [[ROW]]

    out = await providers.free_search("truy vấn", 5, "vi")

    assert out == [{"title": "Tiêu đề", "url": "https://vd.vn", "snippet": "đoạn mô tả"}]
    assert fake_ddgs.calls == 1, "thành công thì không được retry"


async def test_retry_khi_lan_dau_timeout(fake_ddgs):
    """ddgs dừng cả request khi một engine timeout; engine được shuffle nên
    lần thử thứ 2 thường bốc được tổ hợp khác."""
    fake_ddgs.scripted = [TimeoutError("operation timed out"), [ROW]]

    out = await providers.free_search("q", 5, "en")

    assert len(out) == 1
    assert fake_ddgs.calls == 2


async def test_retry_khi_lan_dau_tra_rong(fake_ddgs):
    fake_ddgs.scripted = [[], [ROW]]

    out = await providers.free_search("q", 5, "en")

    assert len(out) == 1
    assert fake_ddgs.calls == 2


async def test_het_retry_thi_bao_loi_ro_rang(fake_ddgs):
    fake_ddgs.scripted = [RuntimeError("bị chặn")]

    with pytest.raises(providers.ProviderError) as err:
        await providers.free_search("q", 5, "en")

    assert "bị chặn" in str(err.value)
    assert fake_ddgs.calls == providers.FREE_RETRIES


async def test_region_vi_duoc_truyen_dung(fake_ddgs, monkeypatch):
    seen = {}

    class Spy(FakeDDGS):
        def text(self, query, **kwargs):
            seen.update(kwargs)
            return [ROW]

    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", Spy)

    await providers.free_search("giá vàng", 3, "vi")
    assert seen["region"] == "vn-vi"
    assert seen["max_results"] == 3


async def test_region_en_duoc_truyen_dung(fake_ddgs, monkeypatch):
    seen = {}

    class Spy(FakeDDGS):
        def text(self, query, **kwargs):
            seen.update(kwargs)
            return [ROW]

    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", Spy)

    await providers.free_search("gold price", 5, "en")
    assert seen["region"] == "us-en"


# --------------------------------------------------------------------------
# serper / tavily — thiếu key thì báo ProviderError để chain đi tiếp
# --------------------------------------------------------------------------


async def test_serper_thieu_key_thi_bao_provider_error(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(providers.ProviderError, match="SERPER_API_KEY"):
        await providers.serper("q", 5, "en")


async def test_tavily_thieu_key_thi_bao_provider_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(providers.ProviderError, match="TAVILY_API_KEY"):
        await providers.tavily("q", 5, "en")


async def test_serper_gan_gl_hl_cho_tieng_viet(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "fake")
    sent = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic": [{"title": "T", "link": "u", "snippet": "s"}]}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.update(json or {})
            return FakeResponse()

    monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)

    out = await providers.serper("giá vàng", 5, "vi")
    assert sent["gl"] == "vn" and sent["hl"] == "vi"
    assert out == [{"title": "T", "url": "u", "snippet": "s"}]


# --------------------------------------------------------------------------
# CHAIN
# --------------------------------------------------------------------------


def test_chain_dung_thu_tu_uu_tien():
    assert [name for name, _ in providers.CHAIN] == ["serper", "tavily", "free"]
