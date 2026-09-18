"""MCP server cung cấp web search cho Claude Code CLI.

Hai tool:
  - web_search: tìm kiếm, trả về title/url/snippet (tiết kiệm token)
  - fetch_page: lấy nội dung đầy đủ của một URL dưới dạng markdown
"""

from __future__ import annotations

import os
import re
import sys
import time

import httpx
from fastmcp import FastMCP

from . import cache, providers

SEARCH_TTL = int(os.getenv("WEB_SEARCH_CACHE_TTL", "3600"))
FETCH_TTL = int(os.getenv("FETCH_CACHE_TTL", "3600"))
MAX_PER_DOMAIN = int(os.getenv("MAX_RESULTS_PER_DOMAIN", "2"))

mcp = FastMCP("web-search")


def _normalize_cache_key(s: str) -> str:
    """Chuẩn hóa key cache để tránh trùng lặp do viết hoa/dấu cách/dấu câu thừa.

    Ví dụ: "Docker  Compose!" và "docker compose" cho cùng cache entry.
    """
    s = s.lower().strip()
    s = re.sub(r'\s+', ' ', s)  # nhiều space → 1 space
    s = re.sub(r'[^\w\s-]', '', s)  # bỏ dấu câu, giữ chữ/số/space/gạch ngang
    return s


def _dedupe_domains(results: list[dict], max_per_domain: int) -> list[dict]:
    """Giới hạn số kết quả từ mỗi domain để tăng đa dạng.

    Ví dụ: docs.docker.com xuất hiện 7×/20 → chỉ giữ 2 cái đầu, bỏ 5 cái sau.
    """
    seen: dict[str, int] = {}
    kept = []
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        # trích domain: https://www.example.com/path → example.com
        domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0].lower()
        count = seen.get(domain, 0)
        if count < max_per_domain:
            kept.append(r)
            seen[domain] = count + 1
    return kept


@mcp.tool
async def web_search(query: str, count: int = 5, lang: str = "auto") -> str:
    """Tìm kiếm trên web và trả về danh sách kết quả.

    Args:
        query: Câu truy vấn tìm kiếm.
        count: Số kết quả mong muốn (1-20).
        lang: "vi" để ưu tiên kết quả tiếng Việt, "en" cho tiếng Anh,
              "auto" để tự phát hiện theo truy vấn.
    """
    count = max(1, min(count, 20))
    if lang == "auto":
        lang = "vi" if any("\u00c0" <= c <= "\u1ef9" for c in query) else "en"

    # Chuẩn hóa query cho cache key để tránh trùng lặp do viết hoa/dấu cách
    norm_query = _normalize_cache_key(query)
    key = f"search:{lang}:{count}:{norm_query}"
    if hit := cache.get(key):
        return _format(hit["provider"], hit["results"], cached=True)

    errors = []
    for name, fn in providers.CHAIN:
        started = time.monotonic()
        try:
            results = await fn(query, count, lang)
        except providers.ProviderError as exc:
            # Thiếu API key là trạng thái bình thường, không tính là lỗi provider.
            errors.append(f"{name}: {exc}")
            continue
        except Exception as exc:
            elapsed = (time.monotonic() - started) * 1000
            print(f"[web-search] {name} thất bại: {exc}", file=sys.stderr)
            cache.record(name, False, elapsed, f"{type(exc).__name__}: {exc}")
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        elapsed = (time.monotonic() - started) * 1000
        if results:
            cache.record(name, True, elapsed)
            # Dedupe domain trước khi cache để không lãng phí slot
            results = _dedupe_domains(results, MAX_PER_DOMAIN)
            cache.set(key, {"provider": name, "results": results}, SEARCH_TTL)
            return _format(name, results)

        cache.record(name, False, elapsed, "không có kết quả")
        errors.append(f"{name}: không có kết quả")

    return "Tìm kiếm thất bại với mọi provider:\n" + "\n".join(f"  - {e}" for e in errors)


@mcp.tool
async def fetch_page(url: str, max_chars: int = 20000) -> str:
    """Lấy nội dung đầy đủ của một trang web dưới dạng markdown.

    Args:
        url: Địa chỉ trang cần đọc.
        max_chars: Số ký tự tối đa trả về (cắt bớt nếu vượt quá).
    """
    key = f"fetch:{url}"
    content = cache.get(key)

    if content is None:
        try:
            content = await _fetch_jina(url)
        except Exception as jina_exc:
            print(f"[web-search] jina thất bại, dùng ddgs: {jina_exc}", file=sys.stderr)
            try:
                content = await _fetch_ddgs(url)
            except Exception as ddgs_exc:
                return (
                    f"Không đọc được {url}.\n"
                    f"  - jina: {jina_exc}\n"
                    f"  - ddgs: {ddgs_exc}"
                )
            if _is_binary(content):
                # ddgs.extract() trả PDF/ảnh ở dạng byte thô: với arxiv PDF là 2.4M
                # ký tự rác. Đưa vào context vừa vô nghĩa vừa tốn token.
                return (
                    f"Không đọc được {url}: nội dung là tệp nhị phân "
                    f"(PDF/ảnh/tài liệu), không phải trang HTML.\n"
                    f"Jina Reader xử lý được định dạng này nhưng hiện lỗi: {jina_exc}\n"
                    f"Hãy thử lại sau, hoặc đặt JINA_API_KEY để tăng rate limit."
                )
        cache.set(key, content, FETCH_TTL)

    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n[...đã cắt bớt, tổng {len(content)} ký tự]"
    return content


async def _fetch_jina(url: str) -> str:
    headers = {"X-Return-Format": "markdown"}
    if token := os.getenv("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(f"https://r.jina.ai/{url}", headers=headers)
        resp.raise_for_status()
        return resp.text


async def _fetch_ddgs(url: str) -> str:
    """Dự phòng khi Jina bị rate limit — ddgs tự tải và convert sang markdown."""
    import anyio
    from ddgs import DDGS

    def _run() -> str:
        return str(DDGS().extract(url, fmt="text_markdown")["content"])

    return await anyio.to_thread.run_sync(_run)


# Magic bytes của các định dạng ddgs.extract() không convert được sang text.
_BINARY_MAGIC = ("%PDF", "PK\x03\x04", "\x89PNG", "GIF8", "\xff\xd8\xff", "%!PS")


def _is_binary(content: str) -> bool:
    """Phát hiện nội dung nhị phân bị ddgs trả về dưới dạng chuỗi."""
    head = content[:1024]
    if head.startswith(_BINARY_MAGIC):
        return True
    if "\x00" in head:
        return True
    # Tỷ lệ ký tự không in được cao => gần như chắc chắn là nhị phân.
    if not head:
        return False
    unprintable = sum(1 for c in head if not c.isprintable() and c not in "\r\n\t")
    return unprintable / len(head) > 0.15


@mcp.tool
async def search_stats(hours: int = 24) -> str:
    """Xem provider nào hay lỗi và chậm bao nhiêu, trong N giờ gần nhất.

    Dùng khi search trả kết quả kém hoặc chậm, để biết nên đặt API key hay không.

    Args:
        hours: Khoảng thời gian thống kê (giờ).
    """
    hours = max(1, min(hours, 720))
    data = cache.stats(hours)
    if not data:
        return f"Chưa có dữ liệu metrics trong {hours} giờ qua."

    lines = [f"Thống kê {hours} giờ qua:\n"]
    lines.append(f"{'provider':<10} {'gọi':>5} {'ok':>4} {'lỗi':>4} {'tỉ lệ':>7} {'TB ms':>8} {'max ms':>8}")
    for name in sorted(data):
        s = data[name]
        lines.append(
            f"{name:<10} {s['calls']:>5} {s['ok']:>4} {s['fail']:>4} "
            f"{s['ok_rate'] * 100:>6.0f}% {s['avg_ms']:>8.0f} {s['max_ms']:>8.0f}"
        )

    if fails := cache.recent_failures(5):
        lines.append("\nLỗi gần nhất:")
        for ts, provider, note in fails:
            when = time.strftime("%H:%M:%S", time.localtime(ts))
            lines.append(f"  {when} {provider}: {note or '(không rõ)'}")

    return "\n".join(lines)


def _format(provider: str, results: list[dict], cached: bool = False) -> str:
    tag = f"{provider} (cache)" if cached else provider
    lines = [f"Nguồn: {tag} — {len(results)} kết quả\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}\n")
    return "\n".join(lines)


def force_utf8() -> None:
    """Ép stdout/stderr sang UTF-8.

    MCP yêu cầu UTF-8; console Windows mặc định cp1252 sẽ làm hỏng ký tự tiếng
    Việt. Stream bị pipe/redirect có thể không hỗ trợ reconfigure — bỏ qua thay
    vì để sập lúc khởi động.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def main() -> None:
    force_utf8()
    mcp.run()


if __name__ == "__main__":
    main()
