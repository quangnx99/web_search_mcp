"""CLI cho web-search-mcp.

Mặc định (không tham số) chạy MCP server trên stdio — giữ nguyên cách MCP client
gọi vào. Các lệnh con chỉ để dùng bằng tay hoặc script hoá:

    web-search-mcp search "giá vàng hôm nay" --count 3
    web-search-mcp fetch https://example.com
    web-search-mcp stats --hours 48
    web-search-mcp doctor

Lệnh con gọi thẳng cùng hàm mà MCP tool dùng, nên cache, retry, fallback và
metrics đều đi qua đúng một đường code.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from . import server

PROG = "web-search-mcp"

# Lệnh con hợp lệ. Cờ -h/--help/--version do argparse lo.
COMMANDS = ("serve", "search", "fetch", "stats", "doctor")

# Dấu hiệu thất bại trong output của tool, để trả exit code khác 0 cho script.
_SEARCH_FAILED = "Tìm kiếm thất bại"
_FETCH_FAILED = "Không đọc được"


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("web-search-mcp-server")
    except PackageNotFoundError:
        # Chạy trực tiếp từ source khi chưa `uv sync`.
        return "0.0.0+source"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="MCP server web search miễn phí, kèm CLI dùng tay.",
    )
    parser.add_argument(
        "--version", action="version", version=f"{PROG} {_version()}"
    )
    sub = parser.add_subparsers(dest="command", metavar="LỆNH")

    sub.add_parser("serve", help="chạy MCP server trên stdio (mặc định)")

    search = sub.add_parser("search", help="tìm kiếm web, in kết quả ra terminal")
    search.add_argument("query", help="câu truy vấn")
    search.add_argument("-n", "--count", type=int, default=5, help="số kết quả (1-20)")
    search.add_argument("-l", "--lang", default="auto", help="vi | en | auto | ...")

    fetch = sub.add_parser("fetch", help="tải một URL thành markdown")
    fetch.add_argument("url", help="địa chỉ trang cần đọc")
    fetch.add_argument(
        "-m", "--max-chars", type=int, default=20000, help="số ký tự tối đa"
    )

    stats = sub.add_parser("stats", help="thống kê provider theo thời gian")
    stats.add_argument("--hours", type=int, default=24, help="khoảng thống kê (giờ)")

    sub.add_parser("doctor", help="kiểm tra cache và biến môi trường")
    return parser


def _cmd_search(args: argparse.Namespace) -> int:
    out = asyncio.run(server.web_search(args.query, count=args.count, lang=args.lang))
    print(out)
    return 1 if out.startswith(_SEARCH_FAILED) else 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    out = asyncio.run(server.fetch_page(args.url, max_chars=args.max_chars))
    print(out)
    return 1 if out.startswith(_FETCH_FAILED) else 0


def _cmd_stats(args: argparse.Namespace) -> int:
    print(asyncio.run(server.search_stats(hours=args.hours)))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from . import cache, providers

    print(f"{PROG} {_version()}")
    print(f"python         {sys.version.split()[0]}")
    print(f"cache db       {cache.db_path()}")
    print(
        f"free tier      backend={providers.FREE_BACKENDS} "
        f"retries={providers.FREE_RETRIES}"
    )
    for name, var in (
        ("serper", "SERPER_API_KEY"),
        ("tavily", "TAVILY_API_KEY"),
        ("jina", "JINA_API_KEY"),
    ):
        # Thiếu key là bình thường: tầng free vẫn chạy.
        print(f"{name:<14} {'đã đặt' if os.getenv(var) else 'chưa đặt (tuỳ chọn)'}")
    return 0


_DISPATCH = {
    "search": _cmd_search,
    "fetch": _cmd_fetch,
    "stats": _cmd_stats,
    "doctor": _cmd_doctor,
}


def main() -> int:
    server.force_utf8()
    argv = sys.argv[1:]

    # MCP client gọi tiến trình với stdio và không truyền lệnh con. Một số client
    # có thể thêm cờ riêng, nên cờ lạ ở vị trí đầu vẫn được coi là ý định chạy
    # server thay vì báo lỗi.
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        return server.main() or 0

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        return server.main() or 0

    try:
        return _DISPATCH[args.command](args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())