# MyPC MCP (`mypc`) — Full-Machine Windows MCP Server over Tailscale

**MyPC MCP** là một máy chủ [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) chạy trên Windows (Python **FastMCP 3.x**, Streamable HTTP), cho phép AI Agent (**ChatGPT Web, Claude Code, Cursor, Gemini**, v.v.) đọc/sửa file, chạy lệnh CLI, quản lý tiến trình, thao tác Git và điều khiển Chrome CDP trên máy tính của bạn **bằng HTTPS qua Tailscale** — không cần thuê VPS, không cần mở port router, không cần cấu hình SSH tunnel phức tạp.

## Tính năng chính

- **50+ công cụ toàn máy (Full-Machine):** Duyệt ổ đĩa/thư mục, đọc file lớn theo dòng, `patch_file` an toàn kèm kiểm tra hash, `grep`, nén/giải nén chống zip-slip, chạy CLI (`exec_command`), quản lý tiến trình nền, `pytest`, Git cục bộ và Chrome DevTools Protocol (`localhost:9222`).
- **Kết nối từ xa siêu gọn bằng Tailscale:**
  - **Tailscale Funnel** (`tailscale funnel --bg 3080`): Tạo URL HTTPS công khai `https://<tên-máy>.<tailnet>.ts.net/mcp` có chứng chỉ TLS tự động — dùng cho **ChatGPT Web, Claude Web, Gemini Web**.
  - **Tailscale Serve** (`tailscale serve --bg 3080`): Chỉ mở HTTPS riêng tư trong mạng **Tailnet** của bạn — dùng cho **Claude Code / Cursor** từ laptop khác.
- **Bảo mật nhiều lớp:**
  - Chạy đúng quyền tài khoản Windows hiện tại (không leo thang đặc quyền).
  - Xác thực **OAuth 2.1 + PKCE + DCR** kèm trang `/consent` yêu cầu nhập **PIN chủ máy** (`MYPC_MCP_CONSENT_PIN`) và allowlist `redirect_uri`.
  - Hỗ trợ song song **Bearer API Key** tĩnh.
  - Ghi **Audit Log** JSONL tại `%LOCALAPPDATA%\MyPCMcp\audit.jsonl` (tự động che toàn bộ token/mật khẩu).

## Kiến trúc kết nối

```text
AI Client (ChatGPT / Claude / Gemini / Cursor)
        │  HTTPS (Auto TLS) + OAuth 2.1 (PKCE + PIN) hoặc Bearer API Key
        ▼
Tailscale Funnel / Serve (https://<machine>.<tailnet>.ts.net)
        │  Reverse proxy cục bộ (không cần VPS, không mở port router)
        ▼
127.0.0.1:3080/mcp  ← MyPC MCP Server (Windows)
```

## Cài đặt tự động bằng AI Agent (Khuyên dùng)

Nếu bạn đang dùng **Claude Code, Cursor, Windsurf, Antigravity, Codex CLI hoặc Gemini CLI**, không cần cài tay — chỉ cần mở thư mục dự án và gửi cho AI câu lệnh sau (xem chi tiết tại [AI_SETUP.md](AI_SETUP.md)):

```text
Hãy đọc file AI_SETUP.md và tự động cài đặt, cấu hình Tailscale, chạy kiểm thử và bàn giao cấu hình MyPC MCP trên máy tính này cho tôi.
```

## Cài đặt thủ công nhanh (3 bước)

### Yêu cầu
- Windows 10/11, Python 3.11+
- [Tailscale](https://tailscale.com/download) đã đăng nhập và bật **MagicDNS** + **HTTPS** (và **Funnel** nếu kết nối ChatGPT/Claude/Gemini Web) trong Tailscale Admin Console.

### Bước 1 — Cài đặt môi trường & tạo `.env`
Mở PowerShell tại thư mục dự án:
```powershell
.\scripts\install.ps1
```
Script sẽ tạo `.venv`, cài thư viện trong `requirements.txt` và tự sinh file `.env` kèm `MYPC_MCP_API_KEY` ngẫu nhiên và mã `MYPC_MCP_CONSENT_PIN` 6 số.

### Bước 2 — Bật Tailscale Funnel (hoặc Serve)
```powershell
# Chế độ Funnel (khuyên dùng cho ChatGPT / Claude / Gemini Web):
.\scripts\tailscale_setup.ps1 -Mode funnel

# Hoặc chế độ Serve (chỉ truy cập nội bộ trong Tailnet của bạn):
.\scripts\tailscale_setup.ps1 -Mode serve
```
Script tự phát hiện URL `https://<machine>.<tailnet>.ts.net`, cập nhật `MYPC_MCP_BASE_URL` trong `.env` và in ra đường dẫn `/mcp` sẵn sàng dán vào AI Client.

### Bước 3 — Khởi chạy server
```bat
start_mypc.bat start    :: chạy ngầm (daemon tự khởi động lại nếu crash)
start_mypc.bat status   :: kiểm tra server + URL Tailscale
start_mypc.bat stop     :: dừng server và tắt Tailscale Funnel/Serve
```
Muốn tự khởi động mỗi khi đăng nhập Windows:
```bat
register_task.bat
```

## Kết nối AI Client

- **ChatGPT Web / Claude Web / Gemini (OAuth):**
  1. Thêm Custom MCP Connector với URL `https://<machine>.<tailnet>.ts.net/mcp`.
  2. Chọn xác thực **OAuth** (server tự quảng bá `/.well-known/oauth-authorization-server` và DCR `/register`).
  3. Khi trình duyệt mở trang `/consent`, nhập mã `MYPC_MCP_CONSENT_PIN` trong `.env` và bấm **Allow Access**.
- **Claude Code / Cursor (Bearer API Key):**
  ```bash
  claude mcp add --transport http mypc https://<machine>.<tailnet>.ts.net/mcp \
    --header "Authorization: Bearer <MYPC_MCP_API_KEY>"
  ```

Xem hướng dẫn chi tiết hơn trong thư mục `docs/`:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/CLIENT_SETUP.md](docs/CLIENT_SETUP.md)
- [docs/SECURITY.md](docs/SECURITY.md)
- [docs/TOOLS.md](docs/TOOLS.md)

## Chạy kiểm thử
```powershell
.venv\Scripts\python.exe -m pytest tests/
```
