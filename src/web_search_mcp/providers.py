"""Các provider tìm kiếm web, xếp theo thứ tự ưu tiên fallback."""

from __future__ import annotations

import os
import sys

import httpx

TIMEOUT = 20.0

# Mặc định "auto" — đo thực tế cho thấy auto ổn định hơn mọi danh sách thủ công
# (5/5 so với 0-3/5), vì auto đẩy wikipedia/grokipedia lên đầu: hai nguồn này gần
# như không bao giờ bị rate limit nên luôn có ít nhất một kết quả.
#
# Cảnh báo khi tự đặt: ddgs giới hạn số engine chạy song song bằng
# min(số_provider, ceil(max_results/10) + 1). Với count=5 chỉ 2 engine được dùng,
# nên liệt kê dài không giúp chống rate limit như trực giác mách bảo.
FREE_BACKENDS = os.getenv("FREE_BACKENDS", "auto")

# ddgs dừng cả request khi một engine timeout (wait FIRST_EXCEPTION). Engine được
# shuffle mỗi lần gọi nên thử lại thường bốc được tổ hợp khác.
FREE_RETRIES = max(1, int(os.getenv("FREE_SEARCH_RETRIES", "2")))

# Mã ngôn ngữ -> (region cho ddgs, gl cho Google/Serper, hl cho Google/Serper).
# Trước đây chỉ có vi/en, mọi thứ khác âm thầm rơi về us-en.
LOCALES = {
    "vi": ("vn-vi", "vn", "vi"),
    "en": ("us-en", "us", "en"),
    "ja": ("jp-jp", "jp", "ja"),
    "ko": ("kr-kr", "kr", "ko"),
    "zh": ("cn-zh", "cn", "zh-cn"),
    "th": ("th-th", "th", "th"),
    "fr": ("fr-fr", "fr", "fr"),
    "de": ("de-de", "de", "de"),
    "es": ("es-es", "es", "es"),
    "ru": ("ru-ru", "ru", "ru"),
}


# Tavily nhận tên nước đầy đủ (không phải mã ISO). "en" cố tình để trống vì
# không cần boost khi tìm tiếng Anh toàn cầu.
COUNTRY_NAMES = {
    "vi": "vietnam",
    "ja": "japan",
    "ko": "south korea",
    "zh": "china",
    "th": "thailand",
    "fr": "france",
    "de": "germany",
    "es": "spain",
    "ru": "russia",
}


def locale(lang: str) -> tuple[str, str, str]:
    """Trả (region, gl, hl) cho mã ngôn ngữ; mã lạ rơi về en."""
    return LOCALES.get(lang, LOCALES["en"])


class ProviderError(Exception):
    """Provider không khả dụng hoặc gọi thất bại."""


def _valid_backends(requested: str) -> str:
    """Lọc bỏ backend không tồn tại trước khi gọi ddgs.

    Cần thiết vì ddgs âm thầm rơi về "auto" khi gặp tên sai (ví dụ "bing" —
    không phải engine, chỉ là provider của duckduckgo/yahoo). Không lọc thì
    cấu hình sai vẫn "chạy" nhưng không đúng thứ tự người dùng khai báo.
    """
    if requested.strip() in ("auto", "all"):
        return requested.strip()

    try:
        from ddgs.engines import ENGINES
    except Exception:  # pragma: no cover - ddgs đổi cấu trúc nội bộ
        return requested

    available = set(ENGINES.get("text", {}))
    asked = [b.strip() for b in requested.split(",") if b.strip()]
    good = [b for b in asked if b in available]
    bad = [b for b in asked if b not in available]

    if bad:
        print(
            f"[web-search] bỏ qua backend không tồn tại: {', '.join(bad)}. "
            f"Hợp lệ: {', '.join(sorted(available))}",
            file=sys.stderr,
        )
    if not good:
        print("[web-search] không còn backend hợp lệ, dùng 'auto'", file=sys.stderr)
        return "auto"
    return ",".join(good)


async def serper(query: str, count: int, lang: str) -> list[dict]:
    """Google SERP qua serper.dev. Chất lượng cao nhất, đặc biệt với tiếng Việt."""
    key = os.getenv("SERPER_API_KEY")
    if not key:
        raise ProviderError("SERPER_API_KEY chưa được đặt")

    _, gl, hl = locale(lang)
    payload = {"q": query, "num": count, "gl": gl, "hl": hl}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return [
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
        for r in data.get("organic", [])[:count]
    ]


async def tavily(query: str, count: int, lang: str) -> list[dict]:
    """Tavily — search tối ưu cho LLM, snippet đã được trích xuất sẵn."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise ProviderError("TAVILY_API_KEY chưa được đặt")

    payload: dict = {"query": query, "max_results": count, "search_depth": "basic"}
    # Tavily boost theo tên nước đầy đủ, không phải mã ISO. Chỉ gửi khi khác "en"
    # để không thu hẹp kết quả không cần thiết.
    # CHƯA VERIFY thực tế: không có TAVILY_API_KEY lúc viết. Nếu API từ chối param
    # này, chain sẽ tự rơi xuống provider "free".
    if country := COUNTRY_NAMES.get(lang):
        payload["country"] = country

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])[:count]
    ]


async def free_search(query: str, count: int, lang: str) -> list[dict]:
    """Tìm kiếm miễn phí qua ddgs, thử lại khi engine bị chặn hoặc timeout."""
    import anyio
    from ddgs import DDGS

    region, _, _ = locale(lang)
    backend = _valid_backends(FREE_BACKENDS)

    def _search() -> list[dict]:
        return DDGS().text(query, region=region, max_results=count, backend=backend)

    last_error = "không rõ nguyên nhân"
    for attempt in range(1, FREE_RETRIES + 1):
        try:
            rows = await anyio.to_thread.run_sync(_search)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            rows = []

        if rows:
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in rows
            ]

        if attempt < FREE_RETRIES:
            print(
                f"[web-search] free lần {attempt}/{FREE_RETRIES} thất bại "
                f"({last_error}), thử lại",
                file=sys.stderr,
            )

    raise ProviderError(f"mọi engine miễn phí đều thất bại sau {FREE_RETRIES} lần ({last_error})")


CHAIN = [("serper", serper), ("tavily", tavily), ("free", free_search)]
