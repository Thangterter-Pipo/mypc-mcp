# Hướng dẫn tự động cài đặt MyPC MCP dành cho AI Agent (`AI_SETUP.md`)

> **Dành cho người dùng:** Mở dự án này bằng AI Agent có quyền chạy lệnh (Claude Code, Cursor, Windsurf, Antigravity, Codex CLI, Gemini CLI...) và dán câu lệnh sau:
>
> ```text
> Hãy đọc file AI_SETUP.md và tự động cài đặt, cấu hình Tailscale, chạy kiểm thử và bàn giao cấu hình MyPC MCP trên máy tính này cho tôi.
> ```

---

## 0. Vai trò & Quy tắc bắt buộc dành cho AI Agent

Bạn đang đóng vai trò kỹ sư tự động hóa cài đặt **MyPC MCP (`mypc`)** trên máy Windows của người dùng. Hãy thực hiện tuần tự từng bước dưới đây, **tự chạy lệnh kiểm tra trước khi hỏi**, và **chỉ dừng lại hỏi người dùng** khi cần lựa chọn chế độ hoặc thao tác đăng nhập trên trình duyệt.

**Quy tắc an toàn:**
1. **Không bao giờ** in toàn bộ `MYPC_MCP_API_KEY` hay nội dung `.env` lên log công khai hoặc commit lên Git. Khi bàn giao cho người dùng cục bộ, chỉ nhắc họ lấy PIN/Key trong file `.env` (hoặc hiển thị cục bộ trong phiên chat riêng tư nếu người dùng yêu cầu).
2. **Không tự cài phần mềm hệ thống** (Python, Tailscale) hay tạo Scheduled Task nếu chưa hỏi xác nhận người dùng.
3. Nếu PowerShell báo lỗi chặn chạy script (`ExecutionPolicy`), luôn chạy kèm `-ExecutionPolicy Bypass`.

---

## Bước 1 — Kiểm tra môi trường (Preflight Check)

Chạy kiểm tra các điều kiện sau trên máy người dùng:

```powershell
# 1. Kiểm tra Python (yêu cầu >= 3.11)
python --version

# 2. Kiểm tra Tailscale CLI
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) {
    $ts = "C:\Program Files\Tailscale\tailscale.exe"
}
if ($ts) { & $ts version } else { Write-Output "TAILSCALE_MISSING" }

# 3. Kiểm tra cổng 3080 có đang bị tiến trình khác chiếm không
netstat -ano | findstr :3080
```

**Xử lý kết quả Preflight:**
- **Thiếu Python hoặc < 3.11:** Hỏi người dùng có muốn cài qua `winget install -e --id Python.Python.3.11` không (hoặc hướng dẫn tải từ https://www.python.org/downloads/ và nhớ tick *Add Python to PATH*).
- **Thiếu Tailscale:** Hỏi người dùng có muốn cài qua `winget install -e --id Tailscale.Tailscale` không (hoặc tải tại https://tailscale.com/download). Sau khi cài xong, nhắc người dùng mở Tailscale và đăng nhập tài khoản.
- **Cổng `3080` đang bận:** Hỏi người dùng muốn chọn cổng khác (ví dụ `3081`) hay kiểm tra tiến trình đang chiếm cổng đó (`tasklist /FI "PID eq <PID>"`).

---

## Bước 2 — Hỏi nhanh 2 lựa chọn trước khi cài

Trước khi chạy cấu hình, hỏi người dùng ngắn gọn (kèm mặc định khuyên dùng):
1. **Chế độ Tailscale:**
   - **`funnel` (mặc định):** Mở HTTPS công khai (`https://<máy>.<tailnet>.ts.net/mcp`) có bảo vệ OAuth + PIN/API Key — **bắt buộc** nếu kết nối **ChatGPT Web, Claude Web, hoặc Gemini Web**.
   - **`serve`:** Chỉ mở HTTPS riêng tư trong mạng Tailnet của người dùng — phù hợp nếu chỉ dùng **Claude Code / Cursor** từ máy tính khác cùng Tailnet.
2. **Phạm vi ổ đĩa (tùy chọn):** Cho phép truy cập tất cả ổ đĩa user có quyền (mặc định), hay giới hạn cụ thể (ví dụ `C:,D:`)?

---

## Bước 3 — Cài đặt `.venv`, thư viện & sinh `.env`

Chạy script cài đặt tự động tại thư mục gốc dự án:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Script sẽ tự động:
- Tạo môi trường ảo `.venv`
- Cài toàn bộ gói trong `requirements.txt`
- Sinh file `.env` từ `.env.example` với `MYPC_MCP_API_KEY` ngẫu nhiên và mã `MYPC_MCP_CONSENT_PIN` 6 chữ số (nếu `.env` chưa tồn tại).

Nếu ở Bước 1–2 người dùng chọn **cổng khác `3080`** hoặc **giới hạn ổ đĩa**, hãy cập nhật tương ứng `MYPC_MCP_PORT` và `MYPC_MCP_ALLOWED_DRIVES` trong file `.env`.

---

## Bước 4 — Bật & cấu hình Tailscale

1. Kiểm tra trạng thái đăng nhập Tailscale:
   ```powershell
   tailscale status --json
   ```
   - Nếu báo chưa đăng nhập (`NeedsLogin` / lỗi kết nối daemon), chạy `tailscale up` hoặc nhắc người dùng đăng nhập ứng dụng Tailscale trên Windows rồi tiếp tục.
   - Kiểm tra trường `Self.DNSName` trong JSON. Nếu rỗng, hướng dẫn người dùng vào **Tailscale Admin Console → DNS** (`https://login.tailscale.com/admin/dns`) bật **MagicDNS** và **HTTPS Certificates**.

2. Chạy script thiết lập Tailscale (thay `<funnel|serve>` và `<PORT>` tương ứng):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\tailscale_setup.ps1 -Mode funnel -Port 3080
   ```
   - **Nếu Tailscale in ra đường link kích hoạt Funnel** (lần đầu dùng Funnel trên tailnet), dừng lại, gửi đúng đường link đó cho người dùng bấm **Enable Funnel** trên trình duyệt, sau đó chạy lại lệnh trên.
   - Xác nhận script đã cập nhật `MYPC_MCP_BASE_URL=https://<machine>.<tailnet>.ts.net` trong `.env`.

---

## Bước 5 — Khởi chạy server & kiểm thử tự động

1. Khởi chạy daemon ở chế độ nền:
   ```cmd
   start_mypc.bat start
   ```
2. Kiểm tra trạng thái:
   ```cmd
   start_mypc.bat status
   ```
   Kỳ vọng: `Local server (127.0.0.1:3080): UP` và hiển thị rõ `Tailscale URL : https://<machine>.<tailnet>.ts.net/mcp`.
3. Chạy kiểm thử tự động (unit test + smoke test `/health` và `/mcp`):
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/ -v
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_mcp.ps1 -Port 3080
   ```
4. Kiểm tra truy cập qua HTTPS Tailscale:
   ```powershell
   # Thay bằng MYPC_MCP_BASE_URL trong .env
   Invoke-RestMethod -Uri "https://<machine>.<tailnet>.ts.net/health" -Method Get
   ```
   Kỳ vọng trả về `{"status": "ok", "active_requests": 0}`.
5. *(Tùy chọn)* Hỏi người dùng có muốn tự khởi động `MyPC MCP` mỗi lần đăng nhập Windows không; nếu đồng ý thì chạy:
   ```cmd
   register_task.bat
   ```

---

## Bước 6 — Bàn giao cấu hình kết nối cho người dùng

Sau khi mọi kiểm thử đều **PASS**, trình bày ngắn gọn cho người dùng:

1. **Thông tin máy chủ đã sẵn sàng:**
   - **MCP Server URL:** `https://<machine>.<tailnet>.ts.net/mcp`
   - **Chế độ Tailscale:** `funnel` (công khai có xác thực) hoặc `serve` (nội bộ Tailnet)
   - **Lệnh quản lý nhanh:** `start_mypc.bat start | status | stop`
   - **Vị trí lấy PIN & API Key:** File `.env` (`MYPC_MCP_CONSENT_PIN` và `MYPC_MCP_API_KEY`).

2. **Cách kết nối nhanh theo từng AI Client:**
   - **ChatGPT Web / Claude Web / Gemini (OAuth):**
     - Thêm Custom MCP Connector với tên `mypc` và URL `https://<machine>.<tailnet>.ts.net/mcp`.
     - Chọn xác thực **OAuth**. Khi trình duyệt mở trang `/consent`, nhập mã `MYPC_MCP_CONSENT_PIN` trong `.env` → bấm **Allow Access**.
   - **Claude Code / Cursor (Bearer API Key):**
     ```bash
     claude mcp add --transport http mypc https://<machine>.<tailnet>.ts.net/mcp \
       --header "Authorization: Bearer <MYPC_MCP_API_KEY>"
     ```

3. **Câu lệnh kiểm tra đầu tiên trong AI Client:**
   > *"Hãy liệt kê các ổ đĩa trên máy tính của tôi."* (Kỳ vọng AI gọi tool `get_roots` thành công.)

---

## Xử lý sự cố nhanh (Troubleshooting cho AI)

| Triệu chứng | Nguyên nhân & Cách xử lý |
| :--- | :--- |
| PowerShell báo `running scripts is disabled` | Luôn thêm `-ExecutionPolicy Bypass` khi gọi các file `.ps1`. |
| `tailscale funnel` báo chưa được phép trên tailnet | Gửi link kích hoạt mà CLI in ra (hoặc `https://login.tailscale.com/admin/acls`) để người dùng bật Funnel, sau đó chạy lại `tailscale_setup.ps1`. |
| `Self.DNSName` bị rỗng | Người dùng cần bật **MagicDNS** và **HTTPS Certificates** tại `https://login.tailscale.com/admin/dns`. |
| `test_mcp.ps1` báo lỗi `401 Unauthorized` | Kiểm tra `MYPC_MCP_API_KEY` trong `.env` không chứa khoảng trắng thừa và server đã được khởi động lại sau khi sửa `.env`. |
| OAuth báo `redirect_uri is not in allowlist` | Thêm prefix redirect URI của client vào biến `MYPC_MCP_OAUTH_REDIRECT_PREFIXES` trong `.env` rồi chạy `start_mypc.bat stop` và `start_mypc.bat start`. |
| ChatGPT/Claude Web không thấy đủ tool mới | Xóa connector cũ, thêm lại connector và mở một đoạn chat mới để làm mới cache schema. |
