# web-search-mcp

MCP server cung cấp web search cho Claude Code CLI. Chạy được **không cần API key nào**.

```bash
uvx web-search-mcp-server     # hoặc: npx -y web-search-mcp
```

## Tools

| Tool | Mô tả |
|---|---|
| `web_search(query, count=5, lang="auto")` | Tìm kiếm web, trả về title/url/snippet |
| `fetch_page(url, max_chars=20000)` | Lấy nội dung trang dưới dạng markdown |
| `search_stats()` | Xem thống kê provider: tỷ lệ thành công, latency, lỗi gần nhất |

`lang="auto"` phát hiện tiếng Việt theo dấu trong truy vấn rồi chuyển sang `vi`
(dùng region `vn-vi`). Truy vấn không dấu như `"Docker Vietnam"` sẽ bị coi là
`en` — đặt `lang="vi"` tường minh nếu cần.

## Provider chain

Server thử lần lượt cho tới khi có kết quả:

1. **Serper.dev** — Google SERP, nhanh nhất (~0.5s). Chỉ dùng nếu có `SERPER_API_KEY`.
2. **Tavily** — search tối ưu cho LLM. Chỉ dùng nếu có `TAVILY_API_KEY`.
3. **`free`** — qua `ddgs`, không cần key. **Đây là mặc định.**

`fetch_page`: thử **Jina Reader** trước, hỏng thì chuyển sang `ddgs.extract()`.

## Về tầng `free` — những gì đã đo được

Mặc định là `FREE_BACKENDS=auto`, và đây là lựa chọn có cơ sở:

| Cấu hình | Thành công (5 query) | Latency TB |
|---|---|---|
| `auto` | **5/5** | 4.2s |
| danh sách thủ công 6 engine | 0/5 | 3.0s |

`auto` thắng vì nó đẩy `wikipedia` + `grokipedia` lên đầu — hai nguồn gần như
không bao giờ bị rate limit, nên luôn có ít nhất một kết quả.

Ba điều phản trực giác về `ddgs`, đều đã kiểm chứng:

- **Liệt kê nhiều engine KHÔNG tăng khả năng chống rate limit.** `ddgs` giới hạn
  số engine chạy song song bằng `min(số_provider, ceil(max_results/10) + 1)`.
  Với `count=5` mặc định thì **chỉ 2 engine** được dùng, bất kể bạn khai bao nhiêu.
- **Một engine timeout làm hỏng cả request** (`wait(..., FIRST_EXCEPTION)`).
  Vì vậy server retry mặc định 2 lần — engine được `ddgs` shuffle mỗi lượt nên
  lần thử lại thường bốc được tổ hợp khác.
- **`bing` không phải backend hợp lệ.** Danh sách thật: `brave`, `duckduckgo`,
  `google`, `grokipedia`, `mojeek`, `startpage`, `wikipedia`, `yahoo`, `yandex`.
  `bing` chỉ là *provider* nằm dưới `duckduckgo`/`yahoo`. Khai tên sai thì `ddgs`
  **âm thầm** rơi về `auto` — server sẽ cảnh báo ra stderr thay vì để bạn tưởng
  cấu hình đang có hiệu lực.

Đánh giá thật: tầng free **dùng tốt cho tra cứu thường ngày**, nhưng latency
3–9s và độ ổn định dao động. Nếu search nhiều, `SERPER_API_KEY` (2.500 query
miễn phí, không cần thẻ) là nâng cấp đáng giá.

## Biến môi trường

Tất cả đều tuỳ chọn.

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `FREE_BACKENDS` | `auto` | Danh sách engine, phân tách bởi dấu phẩy. Tên sai bị lọc kèm cảnh báo |
| `FREE_SEARCH_RETRIES` | `2` | Số lần thử lại tầng free |
| `SERPER_API_KEY` | — | https://serper.dev — 2.500 query miễn phí |
| `TAVILY_API_KEY` | — | https://tavily.com — 1.000 credit/tháng |
| `JINA_API_KEY` | — | Tăng rate limit `fetch_page` |
| `WEB_SEARCH_CACHE_TTL` | `3600` | TTL cache search (giây) |
| `FETCH_CACHE_TTL` | `3600` | TTL cache fetch (giây) |
| `MAX_RESULTS_PER_DOMAIN` | `2` | Số URL tối đa giữ lại cho mỗi domain |
| `WEB_SEARCH_CACHE_DB` | thư mục cache của OS | Đường dẫn file `cache.db` |
| `WEB_SEARCH_CACHE_MAX_ENTRIES` | `500` | Số entry cache tối đa giữ trước khi trim |
| `WEB_SEARCH_STATS_MAX_ROWS` | `1000` | Số bản ghi metrics tối đa (SQLite) |

> Cache mặc định nằm trong thư mục cache theo chuẩn OS
> (`platformdirs.user_cache_dir("web-search-mcp")`), không phải cạnh source —
> quan trọng khi cài qua `uvx`/`npx`, vì môi trường đó bị xoá sau mỗi lần chạy.

## Cài đặt

Cần [uv](https://docs.astral.sh/uv/getting-started/installation/) (đi kèm `uvx`).
`uv` tự lo Python >= 3.12, không cần cài trước.

```bash
uvx web-search-mcp-server             # chạy MCP server trên stdio
claude mcp add --scope user web-search -- uvx web-search-mcp-server
claude mcp list   # kỳ vọng: web-search ... ✔ Connected
```

Hoặc qua npm, nếu bạn quen `npx` (cần Node >= 18):

```bash
npx -y web-search-mcp
claude mcp add --scope user web-search -- npx -y web-search-mcp
```

Gói npm là **shim**: nó gọi `uvx` (hoặc `python -m web_search_mcp` nếu bạn đã
`pip install web-search-mcp-server`) rồi chuyển tiếp stdio nguyên vẹn. Khi máy
chưa có gì chạy được, shim dừng kèm hướng dẫn cài — nó **không** tự tải và chạy
script lạ.

Thêm API key sau khi đã cài:

```bash
claude mcp remove --scope user web-search
claude mcp add --scope user --env SERPER_API_KEY=<key> \
  web-search -- uvx web-search-mcp-server
```

Trên Windows **không cần** bọc `cmd /c`: `uv`/`uvx` là `.exe` thật.

> **Tên gọi:** distribution trên PyPI là `web-search-mcp-server` (tên
> `web-search-mcp` đã có người dùng), lệnh là `web-search-mcp`, còn package npm
> là `web-search-mcp`.

### CLI dùng tay

Server cũng là một CLI bình thường — tiện thử nhanh mà không cần MCP client:

```bash
web-search-mcp search "giá vàng hôm nay" --count 3
web-search-mcp fetch https://example.com --max-chars 4000
web-search-mcp stats --hours 48
web-search-mcp doctor          # in đường dẫn cache + biến môi trường đã đặt
web-search-mcp serve           # MCP server trên stdio (mặc định khi không tham số)
```

`search`/`fetch` trả exit code `1` khi thất bại nên dùng được trong script. Lệnh
con gọi thẳng hàm mà MCP tool dùng — cache, retry, fallback và metrics đi qua
đúng một đường code.

### Chạy từ source

```bash
uv sync
uv run web-search-mcp doctor
```

## Test

```bash
uv sync            # cài pytest ở dependency-group dev
uv run pytest -q   # 99 test, không gọi mạng

cd npm && npm test # 5 test cho shim npm (node --test)
```

Toàn bộ test mock lớp mạng nên chạy offline và deterministic. Trọng tâm phủ:

- `_valid_backends` lọc tên sai (`bing`) và rơi về `auto` khi không còn gì hợp lệ
- retry của `free_search`, kể cả khi engine timeout
- `_is_binary` chặn PDF/ZIP/ảnh mà `ddgs.extract()` trả về dạng byte thô —
  gồm test hồi quy đảm bảo văn bản tiếng Việt **không** bị nhận nhầm là nhị phân
- thứ tự fallback của `fetch_page`, và không cache nội dung lỗi
- cache: TTL, hết hạn, tách entry theo `lang`/`count`, normalize key (lowercase + strip)
- metrics: ghi/đọc, giới hạn dung lượng, windowing theo thời gian
- locale: map `lang` → `(region, gl, hl)` cho DDGS/Serper/Tavily
- dedupe domain: giữ tối đa 2 URL/domain, ưu tiên đa dạng

## Giới hạn đã biết

- `cache.db` chỉ trim bản ghi cũ khi vượt ngưỡng, không có vacuum định kỳ.
