# @quangnx99/web-search-mcp (npm)

Shim npm cho [web-search-mcp-server](https://pypi.org/project/web-search-mcp-server/)
— MCP server web search miễn phí, chạy được **không cần API key**.

Gói này **không chứa code Python**. Nó chỉ tìm cách chạy server và chuyển tiếp
stdio nguyên vẹn cho MCP client:

1. `uvx web-search-mcp-server` (ưu tiên — không cần cài gì trước, chỉ cần `uv`)
2. `uv tool run web-search-mcp-server`
3. `python -m web_search_mcp` (khi đã `pip install web-search-mcp-server`)

Không tìm thấy cách nào thì shim **không tự tải và chạy script lạ** — nó dừng với
hướng dẫn cài `uv`.

## Dùng

```bash
npx @quangnx99/web-search-mcp                       # MCP server trên stdio
npx @quangnx99/web-search-mcp search "giá vàng"     # CLI dùng tay
npx @quangnx99/web-search-mcp doctor                # kiểm tra cache + biến môi trường
```

Nối vào Claude Code:

```bash
claude mcp add --scope user web-search -- npx -y @quangnx99/web-search-mcp
```

## Vì sao cần `uv`?

Server viết bằng Python (dùng `fastmcp` + `ddgs`). Python cần interpreter và một
trình quản lý package; `uv` là cách nhanh nhất và không cần cài sẵn Python.

Tài liệu đầy đủ: xem README của repo.

## Test

```bash
npm test
```

License: MIT