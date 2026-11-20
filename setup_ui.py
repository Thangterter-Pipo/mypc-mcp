# -*- coding: utf-8 -*-
"""
MyPC MCP — Local Web Setup & Control UI (zero external dependencies).

Runs strictly on http://127.0.0.1:3099 (never exposed over Tailscale).
Allows users to install .venv, configure .env (API Key, Consent PIN,
Tailscale Funnel/Serve, allowed drives), start/stop the server, test
connectivity, and copy ready-to-use client configs in one click.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess as sp
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
ENV_EXAMPLE = BASE_DIR / ".env.example"
UI_HOST = "127.0.0.1"
UI_PORT = int(os.environ.get("MYPC_UI_PORT", "3099"))

CREATE_NO_WINDOW = getattr(sp, "CREATE_NO_WINDOW", 0)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _create_job(title: str) -> str:
    job_id = secrets.token_hex(6)
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "title": title,
            "status": "running",
            "progress": 5,
            "stage": "Đang khởi tạo...",
            "logs": [],
            "action_url": "",
        }
    return job_id


def _update_job(job_id: str, *, progress: int | None = None, stage: str | None = None, line: str | None = None, status: str | None = None, action_url: str | None = None) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if progress is not None:
            job["progress"] = max(0, min(100, int(progress)))
        if stage is not None:
            job["stage"] = stage
        if line is not None:
            clean = line.rstrip("\r\n")
            if clean:
                job["logs"].append(clean)
                if len(job["logs"]) > 400:
                    job["logs"] = job["logs"][-400:]
        if status is not None:
            job["status"] = status
        if action_url is not None:
            job["action_url"] = action_url


def _get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        return {
            "id": job["id"],
            "title": job["title"],
            "status": job["status"],
            "progress": job["progress"],
            "stage": job["stage"],
            "log": "\n".join(job["logs"]),
            "action_url": job["action_url"],
        }


def _stream_cmd(cmd: list[str], job_id: str, on_line=None) -> int:
    proc = sp.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).strip()
        if not line:
            continue
        _update_job(job_id, line=line)
        if on_line:
            on_line(line)
    return proc.wait()


def _find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    fallback = r"C:\Program Files\Tailscale\tailscale.exe"
    return fallback if Path(fallback).exists() else None


def _run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = sp.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except sp.TimeoutExpired:
        return -1, "", f"Lệnh vượt quá thời gian chờ ({timeout}s)."
    except Exception as exc:
        return -1, "", str(exc)


def read_env() -> dict[str, str]:
    values: dict[str, str] = {
        "MYPC_MCP_API_KEY": "",
        "MYPC_MCP_CONSENT_PIN": "",
        "MYPC_MCP_AUTO_APPROVE_CHATGPT": "0",
        "MYPC_MCP_HOST": "127.0.0.1",
        "MYPC_MCP_PORT": "3080",
        "MYPC_MCP_TAILSCALE_MODE": "funnel",
        "MYPC_MCP_BASE_URL": "",
        "MYPC_MCP_ROOT": str(Path.home()),
        "MYPC_MCP_ALLOWED_DRIVES": "",
    }
    target = ENV_FILE if ENV_FILE.exists() else ENV_EXAMPLE
    if target.exists():
        for raw in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in values:
                values[k] = v
    return values


def write_env(updates: dict[str, str]) -> dict[str, str]:
    current = read_env()
    for k, v in updates.items():
        if k in current and isinstance(v, str):
            current[k] = v.strip()

    if not current["MYPC_MCP_API_KEY"] or current["MYPC_MCP_API_KEY"].startswith("change-me"):
        current["MYPC_MCP_API_KEY"] = secrets.token_urlsafe(32)
    if not current["MYPC_MCP_CONSENT_PIN"] or current["MYPC_MCP_CONSENT_PIN"].startswith("change-me"):
        current["MYPC_MCP_CONSENT_PIN"] = secrets.token_urlsafe(9)

    lines = [
        "# Generated by MyPC MCP Setup UI",
        f"MYPC_MCP_API_KEY={current['MYPC_MCP_API_KEY']}",
        f"MYPC_MCP_CONSENT_PIN={current['MYPC_MCP_CONSENT_PIN']}",
        f"MYPC_MCP_AUTO_APPROVE_CHATGPT={current['MYPC_MCP_AUTO_APPROVE_CHATGPT']}",
        f"MYPC_MCP_HOST={current['MYPC_MCP_HOST'] or '127.0.0.1'}",
        f"MYPC_MCP_PORT={current['MYPC_MCP_PORT'] or '3080'}",
        f"MYPC_MCP_TAILSCALE_MODE={current['MYPC_MCP_TAILSCALE_MODE'] or 'funnel'}",
        f"MYPC_MCP_BASE_URL={current['MYPC_MCP_BASE_URL']}",
        f"MYPC_MCP_ROOT={current['MYPC_MCP_ROOT'] or str(Path.home())}",
        f"MYPC_MCP_ALLOWED_DRIVES={current['MYPC_MCP_ALLOWED_DRIVES']}",
        "COMMAND_TIMEOUT_SECONDS=120",
        "MAX_COMMAND_OUTPUT_MB=20",
        "MAX_FILE_READ_MB=50",
        "UVICORN_KEEP_ALIVE=20",
        "AUDIT_LOG_ENABLED=true",
        "",
    ]
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return current


def get_tailscale_info() -> dict:
    ts = _find_tailscale()
    if not ts:
        return {"installed": False, "logged_in": False, "dns_name": "", "url": "", "backend_state": "Missing"}
    rc, out, err = _run_cmd([ts, "status", "--json"], timeout=10)
    if rc != 0:
        return {
            "installed": True,
            "logged_in": False,
            "dns_name": "",
            "url": "",
            "backend_state": (err or out or "Error").strip()[:160],
        }
    try:
        data = json.loads(out)
        state = data.get("BackendState", "")
        dns = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
        return {
            "installed": True,
            "logged_in": state == "Running",
            "dns_name": dns,
            "url": f"https://{dns}" if dns else "",
            "backend_state": state,
        }
    except Exception as exc:
        return {"installed": True, "logged_in": False, "dns_name": "", "url": "", "backend_state": str(exc)}


def check_local_health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_system_status() -> dict:
    env = read_env()
    try:
        port = int(env.get("MYPC_MCP_PORT") or "3080")
    except ValueError:
        port = 3080
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    ts_info = get_tailscale_info()
    healthy = check_local_health(port)

    if ts_info["url"] and (not env["MYPC_MCP_BASE_URL"] or "your-machine" in env["MYPC_MCP_BASE_URL"]):
        env["MYPC_MCP_BASE_URL"] = ts_info["url"]

    return {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "venv_ready": venv_py.exists(),
        "env_exists": ENV_FILE.exists(),
        "server_running": healthy,
        "port": port,
        "tailscale": ts_info,
        "env": env,
    }


def start_install_env_job() -> dict:
    job_id = _create_job("Cài đặt môi trường Python (.venv & thư viện)")

    def worker() -> None:
        try:
            venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
            if not venv_py.exists():
                _update_job(job_id, progress=10, stage="Bước 1/3: Đang tạo môi trường ảo .venv...", line="[1/3] Tạo môi trường ảo .venv ...")
                rc = _stream_cmd([sys.executable, "-m", "venv", str(BASE_DIR / ".venv")], job_id)
                if rc != 0:
                    _update_job(job_id, status="error", stage="Lỗi khi tạo .venv.")
                    return
                _update_job(job_id, progress=25, line="Đã tạo .venv thành công.")
            else:
                _update_job(job_id, progress=25, stage="Bước 1/3: Đã có sẵn .venv", line="[1/3] Đã có sẵn .venv.")

            _update_job(job_id, progress=30, stage="Bước 2/3: Đang cài thư viện (pip install)...", line="[2/3] Cài đặt thư viện từ requirements.txt ...")
            step_pct = [30]

            def on_pip_line(line: str) -> None:
                low = line.lower()
                if low.startswith("collecting ") or low.startswith("downloading "):
                    step_pct[0] = min(78, step_pct[0] + 3)
                    _update_job(job_id, progress=step_pct[0], stage=f"Đang tải: {line[:70]}")
                elif "installing collected packages" in low:
                    step_pct[0] = 85
                    _update_job(job_id, progress=85, stage="Đang cài đặt các gói vào .venv...")
                elif "successfully installed" in low:
                    step_pct[0] = 92
                    _update_job(job_id, progress=92, stage="Đã cài xong các thư viện Python.")

            rc = _stream_cmd([str(venv_py), "-m", "pip", "install", "-r", str(BASE_DIR / "requirements.txt")], job_id, on_pip_line)
            if rc != 0:
                _update_job(job_id, status="error", stage="Lỗi khi cài thư viện Python.")
                return

            _update_job(job_id, progress=95, stage="Bước 3/3: Kiểm tra tệp cấu hình .env...", line="[3/3] Kiểm tra file .env ...")
            if not ENV_FILE.exists():
                ts_info = get_tailscale_info()
                write_env({"MYPC_MCP_BASE_URL": ts_info.get("url", "")})
                _update_job(job_id, line="Đã tạo mới .env với API Key và Consent PIN ngẫu nhiên.")
            else:
                _update_job(job_id, line="Giữ nguyên file .env hiện có.")

            _update_job(job_id, progress=100, status="done", stage="Hoàn tất cài đặt môi trường Python!")
        except Exception as exc:
            _update_job(job_id, status="error", stage=f"Lỗi: {exc}", line=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "job_id": job_id, "log": "Đã bắt đầu cài đặt môi trường Python..."}


TAILSCALE_INSTALLER_URL = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"


def start_tailscale_install_job() -> dict:
    if _find_tailscale():
        return {"ok": True, "log": "Tailscale đã được cài đặt sẵn trên máy."}
    job_id = _create_job("Tải & Cài đặt Tailscale (chính thức)")

    def worker() -> None:
        try:
            import tempfile

            dest = Path(tempfile.gettempdir()) / "tailscale-setup-latest.exe"
            _update_job(
                job_id,
                progress=5,
                stage="Đang kết nối máy chủ pkgs.tailscale.com...",
                line=f"Tải trực tiếp: {TAILSCALE_INSTALLER_URL}",
            )
            req = urllib.request.Request(
                TAILSCALE_INSTALLER_URL,
                headers={"User-Agent": "MyPC-MCP-Setup/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or "0")
                downloaded = 0
                last_report = 0.0
                with dest.open("wb") as out_f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 0.25:
                            last_report = now
                            mb_done = downloaded / (1024 * 1024)
                            if total > 0:
                                mb_total = total / (1024 * 1024)
                                dl_pct = int(downloaded * 100 / total)
                                mapped = 5 + int(dl_pct * 0.75)
                                _update_job(
                                    job_id,
                                    progress=min(80, mapped),
                                    stage=f"Đang tải bộ cài Tailscale: {mb_done:.1f} / {mb_total:.1f} MB ({dl_pct}%)",
                                )
                            else:
                                _update_job(
                                    job_id,
                                    progress=45,
                                    stage=f"Đang tải bộ cài Tailscale: {mb_done:.1f} MB...",
                                )

            mb_final = dest.stat().st_size / (1024 * 1024)
            _update_job(
                job_id,
                progress=82,
                stage="Tải xong! Đang mở trình cài đặt Tailscale...",
                line=f"Đã tải xong {mb_final:.1f} MB -> {dest}",
            )
            os.startfile(str(dest))  # launches with normal Windows UAC prompt
            _update_job(
                job_id,
                progress=88,
                stage="Đang chờ bạn bấm Next/Install trong cửa sổ cài đặt Tailscale...",
                line="Cửa sổ cài đặt Tailscale đã mở. Hãy xác nhận UAC và hoàn tất cài đặt...",
            )

            # Wait up to 3 minutes for tailscale.exe to appear
            for _ in range(120):
                if _find_tailscale():
                    _update_job(
                        job_id,
                        progress=100,
                        status="done",
                        stage="Đã cài đặt Tailscale thành công! Hãy bấm 'Đăng nhập Tailscale'.",
                        line="Phát hiện Tailscale CLI đã sẵn sàng trên máy!",
                    )
                    return
                time.sleep(1.5)

            _update_job(
                job_id,
                progress=90,
                status="done",
                stage="Bộ cài đã mở. Sau khi cài xong, bấm 'Làm mới trạng thái'.",
                line="Hết thời gian chờ tự động (3 phút). Nếu bạn vừa hoàn tất cài đặt, bấm 'Làm mới trạng thái'.",
            )
        except Exception as exc:
            _update_job(
                job_id,
                status="error",
                stage=f"Lỗi tải/cài Tailscale: {exc}",
                line=f"Lỗi: {exc}\nBạn có thể bấm 'Tải bộ cài thủ công' (https://tailscale.com/download).",
            )

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "job_id": job_id, "log": "Đang bắt đầu tải bộ cài chính thức Tailscale..."}


def action_tailscale_login() -> dict:
    ts = _find_tailscale()
    if not ts:
        return {"ok": False, "log": "Chưa cài Tailscale. Hãy cài Tailscale trước."}
    ts_info = get_tailscale_info()
    if ts_info["logged_in"]:
        return {"ok": True, "log": f"Tailscale đã đăng nhập sẵn ({ts_info['dns_name'] or 'Running'})."}
    gui = Path(r"C:\Program Files\Tailscale\tailscale-ipn.exe")
    if gui.exists():
        try:
            sp.Popen([str(gui)], creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    rc, out, err = _run_cmd([ts, "login"], timeout=15)
    combined = (out + "\n" + err).strip()
    urls = re.findall(r"https://login\.tailscale\.com/\S+", combined)
    if urls:
        try:
            webbrowser.open(urls[0])
        except Exception:
            pass
        return {
            "ok": True,
            "log": f"Đã mở trình duyệt đăng nhập Tailscale:\n{urls[0]}\nSau khi đăng nhập xong, bấm 'Làm mới trạng thái'.",
            "action_url": urls[0],
        }
    return {
        "ok": rc == 0,
        "log": combined or "Đã mở ứng dụng Tailscale. Hãy đăng nhập từ biểu tượng Tailscale dưới khay hệ thống rồi bấm 'Làm mới trạng thái'.",
    }


def action_tailscale_enable(mode: str, port: int) -> dict:
    ts = _find_tailscale()
    if not ts:
        return {"ok": False, "log": "Chưa tìm thấy Tailscale CLI. Hãy tải tại https://tailscale.com/download"}

    mode = mode.lower().strip()
    if mode not in ("funnel", "serve", "none"):
        return {"ok": False, "log": f"Chế độ không hợp lệ: {mode}"}

    if mode == "none":
        _run_cmd([ts, "funnel", "--https=443", "off"], timeout=15)
        _run_cmd([ts, "serve", "--https=443", "off"], timeout=15)
        write_env({"MYPC_MCP_TAILSCALE_MODE": "none", "MYPC_MCP_PORT": str(port)})
        return {"ok": True, "log": "Đã tắt Tailscale Funnel/Serve (chạy chế độ Local only)."}

    ts_info = get_tailscale_info()
    if not ts_info["logged_in"]:
        return {"ok": False, "log": "Tailscale chưa đăng nhập. Hãy mở ứng dụng Tailscale trên Windows và đăng nhập trước."}
    if not ts_info["url"]:
        return {
            "ok": False,
            "log": "Không tìm thấy MagicDNS URL. Hãy vào https://login.tailscale.com/admin/dns bật MagicDNS và HTTPS Certificates.",
        }

    rc, out, err = _run_cmd([ts, mode, "--bg", str(port)], timeout=25)
    combined = (out + "\n" + err).strip()
    urls = re.findall(r"https://login\.tailscale\.com/\S+", combined)
    if rc == 0:
        write_env({
            "MYPC_MCP_TAILSCALE_MODE": mode,
            "MYPC_MCP_PORT": str(port),
            "MYPC_MCP_BASE_URL": ts_info["url"],
        })
        return {
            "ok": True,
            "log": f"Đã bật tailscale {mode} --bg {port}\nEndpoint: {ts_info['url']}/mcp\n{combined}".strip(),
            "action_url": urls[0] if urls else "",
        }
    return {
        "ok": False,
        "log": combined or f"tailscale {mode} trả về mã lỗi {rc}",
        "action_url": urls[0] if urls else "",
    }


def action_start_server() -> dict:
    if not ENV_FILE.exists():
        write_env({})
    venv_pyw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    py = venv_pyw if venv_pyw.exists() else (venv_py if venv_py.exists() else Path(sys.executable))

    try:
        sp.Popen(
            [str(py), str(BASE_DIR / "start_mcp.py")],
            cwd=str(BASE_DIR),
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return {"ok": False, "log": f"Không thể khởi chạy server: {exc}"}

    env = read_env()
    port = int(env.get("MYPC_MCP_PORT") or "3080")
    for _ in range(16):
        if check_local_health(port):
            return {"ok": True, "log": f"MyPC MCP đã khởi chạy thành công trên 127.0.0.1:{port}."}
        time.sleep(0.5)
    return {
        "ok": False,
        "log": "Đã gửi lệnh khởi chạy nhưng chưa thấy /health phản hồi. Kiểm tra log tại %LOCALAPPDATA%\\MyPCMcp\\server.log.",
    }


def action_stop_server() -> dict:
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    py = str(venv_py) if venv_py.exists() else sys.executable
    rc, out, err = _run_cmd([py, str(BASE_DIR / "start_mcp.py"), "stop"], timeout=20)
    return {"ok": rc == 0, "log": (out + "\n" + err).strip() or "Đã dừng server."}


def action_test_connection() -> dict:
    env = read_env()
    port = int(env.get("MYPC_MCP_PORT") or "3080")
    api_key = env.get("MYPC_MCP_API_KEY", "")
    logs: list[str] = []

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4) as r:
            logs.append(f"[Local /health] HTTP {r.status} -> {r.read().decode('utf-8', 'ignore')}")
    except Exception as exc:
        return {"ok": False, "log": f"[Local /health] Lỗi: {exc}\nHãy bấm 'Khởi chạy Server' trước."}

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "mypc-setup-ui", "version": "1.0"},
        },
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            sid = r.headers.get("Mcp-Session-Id", "")
            logs.append(f"[Local /mcp initialize] HTTP {r.status} | Mcp-Session-Id: {sid or '(ok)'}")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "log": "\n".join(logs + [f"[Local /mcp] HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"])}
    except Exception as exc:
        return {"ok": False, "log": "\n".join(logs + [f"[Local /mcp] Lỗi: {exc}"])}

    base_url = env.get("MYPC_MCP_BASE_URL", "").rstrip("/")
    if base_url.startswith("https://") and "your-machine" not in base_url:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=6) as r:
                logs.append(f"[Tailscale {base_url}/health] HTTP {r.status} -> OK")
        except Exception as exc:
            logs.append(f"[Tailscale {base_url}/health] Chưa truy cập được qua HTTPS: {exc}")

    return {"ok": True, "log": "\n".join(logs)}


def action_register_autostart() -> dict:
    rc, out, err = _run_cmd(["cmd", "/c", str(BASE_DIR / "register_task.bat")], timeout=20)
    return {"ok": rc == 0, "log": (out + "\n" + err).strip()}


HTML_PAGE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyPC MCP — Setup & Control Panel</title>
<style>
  :root {
    --bg: #0c0f16; --card: #151923; --border: #252c3b; --text: #e6edf3;
    --muted: #8b949e; --accent: #3b82f6; --accent-h: #2563eb;
    --ok: #22c55e; --warn: #f59e0b; --err: #ef4444; --input: #0b0e14;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; overflow: hidden; font-family: "Segoe UI", system-ui, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; }
  .app { height: 100vh; display: flex; flex-direction: column; padding: 12px 16px; gap: 10px; max-width: 1440px; margin: 0 auto; }
  .topbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 8px 14px; flex-shrink: 0; }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand h1 { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.02em; }
  .brand .tag { font-size: 11px; padding: 2px 7px; border-radius: 999px; background: rgba(59,130,246,.16); color: #60a5fa; font-weight: 600; }
  .pills { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: var(--input); border: 1px solid var(--border); font-size: 12px; font-weight: 500; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .dot.ok { background: var(--ok); box-shadow: 0 0 7px var(--ok); }
  .dot.warn { background: var(--warn); }
  .dot.err { background: var(--err); }
  .main { flex: 1; min-height: 0; display: grid; grid-template-columns: 1.08fr 0.92fr; gap: 10px; }
  .col { display: flex; flex-direction: column; gap: 10px; min-height: 0; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 11px; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .card.grow { flex: 1; min-height: 0; }
  .card h2 { margin: 0; font-size: 13.5px; font-weight: 700; display: flex; align-items: center; gap: 7px; }
  .step { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 6px; background: rgba(59,130,246,.18); color: #60a5fa; font-size: 11.5px; font-weight: 700; flex-shrink: 0; }
  .row-setup { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 10px; background: var(--input); border: 1px solid var(--border); border-radius: 8px; flex-wrap: wrap; }
  .row-setup .lbl { font-weight: 600; font-size: 12.5px; }
  .row-setup .sub { font-size: 11.5px; color: var(--muted); }
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 10px; }
  .span2 { grid-column: span 2; }
  label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px; font-weight: 500; }
  input, select { width: 100%; padding: 6px 9px; border-radius: 7px; border: 1px solid var(--border); background: var(--input); color: var(--text); font-size: 12.5px; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .input-row { display: flex; gap: 4px; align-items: center; }
  .input-row input { flex: 1; min-width: 0; }
  .btns { display: flex; flex-wrap: wrap; gap: 6px; }
  button { padding: 6px 11px; border-radius: 7px; border: 1px solid var(--border); background: #212736; color: var(--text); font-size: 12px; font-weight: 600; cursor: pointer; transition: .12s; white-space: nowrap; }
  button:hover { background: #2b3347; }
  button:disabled { opacity: .5; cursor: wait; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.primary:hover { background: var(--accent-h); }
  button.success { background: #16a34a; border-color: #16a34a; color: #fff; }
  button.danger { background: rgba(239,68,68,.16); border-color: rgba(239,68,68,.4); color: #fca5a5; }
  button.xs { padding: 5px 7px; font-size: 11px; }
  .ctrl-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
  .codebox { background: var(--input); border: 1px solid var(--border); border-radius: 7px; padding: 7px 10px; font-family: Consolas, monospace; font-size: 11.5px; word-break: break-all; white-space: pre-wrap; line-height: 1.45; }
  .endpoint { color: #93c5fd; font-weight: 600; font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tabs { display: flex; gap: 6px; }
  .tab { padding: 4px 10px; border-radius: 6px; font-size: 11.5px; background: var(--input); border: 1px solid var(--border); cursor: pointer; color: var(--muted); font-weight: 600; }
  .tab.active { background: rgba(59,130,246,.18); border-color: var(--accent); color: #93c5fd; }
  .prog-wrap { background: #0f131d; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; flex-shrink: 0; }
  .prog-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin-bottom: 5px; gap: 8px; }
  .prog-title { font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .prog-pct { font-family: Consolas, monospace; font-weight: 700; color: #60a5fa; flex-shrink: 0; }
  .prog-track { width: 100%; height: 7px; background: #1e2433; border-radius: 999px; overflow: hidden; }
  .prog-bar { height: 100%; width: 0%; background: linear-gradient(90deg, #3b82f6, #60a5fa); border-radius: 999px; transition: width .3s ease; }
  .prog-bar.done { background: linear-gradient(90deg, #16a34a, #22c55e); }
  .prog-bar.error { background: linear-gradient(90deg, #dc2626, #ef4444); }
  .prog-stage { font-size: 11px; color: var(--muted); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .console { flex: 1; min-height: 60px; background: #080a0f; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-family: Consolas, monospace; font-size: 11.5px; color: #cbd5e1; overflow-y: auto; white-space: pre-wrap; line-height: 1.4; }
  .banner { display: none; padding: 6px 10px; border-radius: 7px; font-size: 11.5px; background: rgba(245,158,11,.14); border: 1px solid rgba(245,158,11,.4); color: #fde68a; }
  .banner a { color: #60a5fa; font-weight: 600; }
  @media (max-width: 960px) {
    html, body { overflow: auto; }
    .app { height: auto; }
    .main { grid-template-columns: 1fr; }
    .ctrl-grid { grid-template-columns: repeat(2, 1fr); }
  }
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <div class="brand">
      <h1>MyPC MCP</h1>
      <span class="tag">127.0.0.1 Only</span>
    </div>
    <div class="pills">
      <div class="pill" id="st-py"><span class="dot warn"></span>Python...</div>
      <div class="pill" id="st-ts"><span class="dot warn"></span>Tailscale...</div>
      <div class="pill" id="st-srv"><span class="dot warn"></span>Server...</div>
      <div class="pill" id="st-env"><span class="dot warn"></span>.env...</div>
      <button class="xs" onclick="refreshStatus()">Làm mới</button>
    </div>
  </header>

  <div class="main">
    <div class="col">
      <div class="card">
        <h2><span class="step">1</span> Chuẩn bị Python &amp; Tailscale</h2>
        <div class="row-setup">
          <div><div class="lbl">Môi trường Python</div><div class="sub">Tạo .venv, cài thư viện &amp; sinh .env</div></div>
          <button class="primary" onclick="runAction('/api/install', {}, this)">Cài .venv &amp; Thư viện</button>
        </div>
        <div class="row-setup">
          <div><div class="lbl">Tailscale (HTTPS không cần VPS)</div><div class="sub">Tải trực tiếp bộ cài chính thức, đăng nhập &amp; bật MagicDNS</div></div>
          <div class="btns">
            <button class="primary" onclick="runAction('/api/tailscale-install', {}, this)">Tải &amp; Cài Tailscale</button>
            <button class="success" onclick="runAction('/api/tailscale-login', {}, this)">Đăng nhập</button>
            <button onclick="window.open('https://login.tailscale.com/admin/dns', '_blank')">MagicDNS</button>
            <button onclick="window.open('https://tailscale.com/download', '_blank')">Tải tay</button>
          </div>
        </div>
      </div>

      <div class="card grow">
        <h2><span class="step">2</span> Cấu hình bảo mật &amp; Chế độ Tailscale (.env)</h2>
        <div class="form-grid">
          <div>
            <label>Chế độ Tailscale</label>
            <select id="f-mode">
              <option value="funnel">Funnel — Công khai (ChatGPT/Claude/Gemini Web)</option>
              <option value="serve">Serve — Nội bộ Tailnet (Claude Code/Cursor)</option>
              <option value="none">Local only — Chỉ 127.0.0.1</option>
            </select>
          </div>
          <div>
            <label>Cổng cục bộ (Port)</label>
            <input id="f-port" type="number" value="3080">
          </div>
          <div class="span2">
            <label>Consent PIN (nhập khi duyệt OAuth)</label>
            <div class="input-row">
              <input id="f-pin" type="password" placeholder="Nhập PIN mạnh...">
              <button class="xs" onclick="toggleVis('f-pin')">Hiện</button>
              <button class="xs" onclick="genSecret('f-pin', 12)">Sinh mới</button>
              <button class="xs" onclick="copyVal('f-pin')">Copy</button>
            </div>
          </div>
          <div class="span2">
            <label>Static Bearer API Key</label>
            <div class="input-row">
              <input id="f-key" type="password" placeholder="Bearer API key...">
              <button class="xs" onclick="toggleVis('f-key')">Hiện</button>
              <button class="xs" onclick="genSecret('f-key', 32)">Sinh mới</button>
              <button class="xs" onclick="copyVal('f-key')">Copy</button>
            </div>
          </div>
          <div>
            <label>Giới hạn ổ đĩa (vd: C:,D: — trống = tất cả)</label>
            <input id="f-drives" placeholder="Tất cả ổ đĩa">
          </div>
          <div>
            <label>Tailscale HTTPS URL</label>
            <input id="f-baseurl" placeholder="https://my-pc.tailnet.ts.net">
          </div>
        </div>
        <div class="btns" style="margin-top:auto">
          <button class="primary" onclick="saveConfig(this)">Lưu cấu hình .env</button>
          <button onclick="enableTailscale(this)">Áp dụng Tailscale (Funnel / Serve)</button>
        </div>
        <div id="ts-banner" class="banner"></div>
      </div>
    </div>

    <div class="col">
      <div class="card">
        <h2><span class="step">3</span> Điều khiển Server</h2>
        <div class="ctrl-grid">
          <button class="success" onclick="runAction('/api/start', {}, this)">Khởi chạy</button>
          <button class="danger" onclick="runAction('/api/stop', {}, this)">Dừng</button>
          <button onclick="runAction('/api/test', {}, this)">Test kết nối</button>
          <button onclick="runAction('/api/autostart', {}, this)">Tự chạy Win</button>
        </div>
      </div>

      <div class="card">
        <h2><span class="step">4</span> Kết nối AI Client (Copy 1-Click)</h2>
        <div class="input-row">
          <div class="codebox endpoint" id="out-url">Chưa phát hiện URL Tailscale...</div>
          <button class="xs primary" onclick="copyText(document.getElementById('out-url').textContent)">Copy URL</button>
        </div>
        <div class="tabs">
          <div class="tab active" onclick="switchTab('oauth', this)">ChatGPT / Claude / Gemini (OAuth)</div>
          <div class="tab" onclick="switchTab('cli', this)">Claude Code / Cursor (CLI)</div>
        </div>
        <div id="tab-oauth" class="codebox">1. Thêm Custom MCP Connector tên "mypc" với URL ở trên, chọn OAuth.
2. Nhập Consent PIN (Bước 2) tại trang /consent → Allow Access.</div>
        <div id="tab-cli" style="display:none">
          <div class="input-row">
            <div class="codebox" id="out-cli" style="flex:1"></div>
            <button class="xs" onclick="copyText(document.getElementById('out-cli').textContent)">Copy</button>
          </div>
        </div>
      </div>

      <div class="card grow">
        <h2>Tiến trình &amp; Nhật ký (Console)</h2>
        <div class="prog-wrap" id="prog-wrap">
          <div class="prog-head">
            <span class="prog-title" id="prog-title">Sẵn sàng</span>
            <span class="prog-pct" id="prog-pct">0%</span>
          </div>
          <div class="prog-track"><div class="prog-bar" id="prog-bar"></div></div>
          <div class="prog-stage" id="prog-stage">Bấm một nút cài đặt ở cột trái để theo dõi tiến trình trực tiếp.</div>
        </div>
        <div class="console" id="console">Sẵn sàng. Làm lần lượt từ Bước 1 → Bước 4.</div>
      </div>
    </div>
  </div>
</div>

<script>
function logMsg(text) {
  const el = document.getElementById('console');
  el.textContent = text;
  el.scrollTop = el.scrollHeight;
}
function toggleVis(id) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
}
function copyText(txt) {
  navigator.clipboard.writeText(txt).then(() => logMsg('Đã copy vào clipboard.'));
}
function copyVal(id) { copyText(document.getElementById(id).value); }
function genSecret(id, bytes) {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  const s = btoa(String.fromCharCode(...arr)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  const el = document.getElementById(id);
  el.value = s; el.type = 'text';
}
function switchTab(name, btn) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-oauth').style.display = name === 'oauth' ? 'block' : 'none';
  document.getElementById('tab-cli').style.display = name === 'cli' ? 'block' : 'none';
}
function updateClientSnippets() {
  const base = (document.getElementById('f-baseurl').value || '').replace(/\/+$/, '');
  const port = document.getElementById('f-port').value || '3080';
  const key = document.getElementById('f-key').value || '<MYPC_MCP_API_KEY>';
  const url = base ? `${base}/mcp` : `http://127.0.0.1:${port}/mcp`;
  document.getElementById('out-url').textContent = url;
  document.getElementById('out-cli').textContent =
    `claude mcp add --transport http mypc ${url} --header "Authorization: Bearer ${key}"`;
}
async function refreshStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();
  document.getElementById('st-py').innerHTML =
    `<span class="dot ${d.venv_ready ? 'ok' : 'warn'}"></span>Py ${d.python_version} (${d.venv_ready ? '.venv' : 'chưa cài'})`;
  document.getElementById('st-ts').innerHTML =
    `<span class="dot ${d.tailscale.logged_in ? 'ok' : (d.tailscale.installed ? 'warn' : 'err')}"></span>` +
    (d.tailscale.logged_in ? 'Tailscale OK' : (d.tailscale.installed ? 'TS chưa login' : 'Chưa có TS'));
  document.getElementById('st-srv').innerHTML =
    `<span class="dot ${d.server_running ? 'ok' : 'err'}"></span>${d.server_running ? 'Server :' + d.port : 'Server tắt'}`;
  document.getElementById('st-env').innerHTML =
    `<span class="dot ${d.env_exists ? 'ok' : 'warn'}"></span>${d.env_exists ? '.env OK' : 'Chưa có .env'}`;

  document.getElementById('f-mode').value = d.env.MYPC_MCP_TAILSCALE_MODE || 'funnel';
  document.getElementById('f-port').value = d.env.MYPC_MCP_PORT || '3080';
  document.getElementById('f-pin').value = d.env.MYPC_MCP_CONSENT_PIN || '';
  document.getElementById('f-key').value = d.env.MYPC_MCP_API_KEY || '';
  document.getElementById('f-drives').value = d.env.MYPC_MCP_ALLOWED_DRIVES || '';
  document.getElementById('f-baseurl').value = d.env.MYPC_MCP_BASE_URL || d.tailscale.url || '';
  updateClientSnippets();
}
function setProgress(title, pct, stage, status) {
  document.getElementById('prog-title').textContent = title || 'Đang thực thi...';
  document.getElementById('prog-pct').textContent = `${pct}%`;
  document.getElementById('prog-stage').textContent = stage || '';
  const bar = document.getElementById('prog-bar');
  bar.style.width = `${pct}%`;
  bar.className = 'prog-bar' + (status === 'done' ? ' done' : (status === 'error' ? ' error' : ''));
}
async function pollJob(jobId, btn) {
  try {
    while (true) {
      const r = await fetch(`/api/job/${jobId}`);
      const j = await r.json();
      setProgress(j.title, j.progress, j.stage, j.status);
      if (j.log) logMsg(j.log);
      if (j.status === 'done' || j.status === 'error') {
        await refreshStatus();
        break;
      }
      await new Promise(res => setTimeout(res, 400));
    }
  } catch (e) {
    logMsg('[LỖI] ' + e);
  } finally {
    if (btn) btn.disabled = false;
  }
}
async function runAction(path, body, btn) {
  if (btn) btn.disabled = true;
  logMsg('Đang thực thi...');
  try {
    const r = await fetch(path, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.job_id) {
      await pollJob(d.job_id, btn);
      return;
    }
    logMsg((d.ok ? '[OK] ' : '[LỖI] ') + (d.log || 'Hoàn tất.'));
    const banner = document.getElementById('ts-banner');
    if (d.action_url) {
      banner.style.display = 'block';
      banner.innerHTML = `Cần thao tác trên Tailscale: <a href="${d.action_url}" target="_blank" rel="noopener">Mở liên kết Tailscale</a>, sau đó bấm Làm mới.`;
    } else {
      banner.style.display = 'none';
    }
    await refreshStatus();
    if (btn) btn.disabled = false;
  } catch (e) {
    logMsg('[LỖI] ' + e);
    if (btn) btn.disabled = false;
  }
}
function saveConfig(btn) {
  runAction('/api/save-env', {
    MYPC_MCP_TAILSCALE_MODE: document.getElementById('f-mode').value,
    MYPC_MCP_PORT: document.getElementById('f-port').value,
    MYPC_MCP_CONSENT_PIN: document.getElementById('f-pin').value,
    MYPC_MCP_API_KEY: document.getElementById('f-key').value,
    MYPC_MCP_ALLOWED_DRIVES: document.getElementById('f-drives').value,
    MYPC_MCP_BASE_URL: document.getElementById('f-baseurl').value,
  }, btn);
}
function enableTailscale(btn) {
  runAction('/api/tailscale', {
    mode: document.getElementById('f-mode').value,
    port: parseInt(document.getElementById('f-port').value || '3080', 10),
  }, btn);
}
refreshStatus();
</script>
</body>
</html>
"""


class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return  # keep console clean

    def _is_local_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]", "")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if not self._is_local_host():
            self.send_error(HTTPStatus.FORBIDDEN, "Localhost only")
            return
        if self.path in ("/", "/index.html"):
            raw = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/api/status":
            self._send_json(get_system_status())
            return
        if self.path.startswith("/api/job/"):
            job_id = self.path.split("/api/job/", 1)[1].strip("/")
            job = _get_job(job_id)
            if job is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._send_json(job)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._is_local_host():
            self.send_error(HTTPStatus.FORBIDDEN, "Localhost only")
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(min(length, 65536)).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {}

        if self.path == "/api/install":
            self._send_json(start_install_env_job())
        elif self.path == "/api/tailscale-install":
            self._send_json(start_tailscale_install_job())
        elif self.path == "/api/tailscale-login":
            self._send_json(action_tailscale_login())
        elif self.path == "/api/save-env":
            write_env(body)
            self._send_json({"ok": True, "log": "Đã lưu cấu hình vào file .env."})
        elif self.path == "/api/tailscale":
            self._send_json(action_tailscale_enable(str(body.get("mode", "funnel")), int(body.get("port", 3080))))
        elif self.path == "/api/start":
            self._send_json(action_start_server())
        elif self.path == "/api/stop":
            self._send_json(action_stop_server())
        elif self.path == "/api/test":
            self._send_json(action_test_connection())
        elif self.path == "/api/autostart":
            self._send_json(action_register_autostart())
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    server = ThreadingHTTPServer((UI_HOST, UI_PORT), SetupHandler)
    url = f"http://{UI_HOST}:{UI_PORT}"
    print(f"MyPC MCP Setup UI đang chạy tại {url}")
    print("Nhấn Ctrl+C để đóng giao diện cài đặt.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã đóng Setup UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
