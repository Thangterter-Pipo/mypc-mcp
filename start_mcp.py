# -*- coding: utf-8 -*-
"""
Daemon supervisor for MyPC MCP server + Tailscale Funnel/Serve.

Usage:
    python start_mcp.py          # run supervisor (keeps server + Tailscale route up)
    python start_mcp.py status   # check server health, Tailscale status & public URL
    python start_mcp.py stop     # stop local server and disable Tailscale route
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess as sp
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

VENV_PYTHONW = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
RUN_PY = BASE_DIR / "run.py"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MyPCMcp"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOCK_FILE = LOG_DIR / "supervisor.lock"
PID_FILE = LOG_DIR / "supervisor.pid"
SUP_LOG = LOG_DIR / "supervisor.log"
SERVER_LOG = LOG_DIR / "server.log"

HOST = os.environ.get("MYPC_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MYPC_MCP_PORT", "3080"))
TAILSCALE_MODE = os.environ.get("MYPC_MCP_TAILSCALE_MODE", "funnel").strip().lower()
TAILSCALE_BIN = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with SUP_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _ts_exists() -> bool:
    return bool(TAILSCALE_BIN and Path(TAILSCALE_BIN).exists())


def get_tailscale_url() -> str | None:
    """Return https://<node>.<tailnet>.ts.net if Tailscale is logged in."""
    if not _ts_exists():
        return None
    try:
        out = sp.run(
            [TAILSCALE_BIN, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        dns = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
        return f"https://{dns}" if dns else None
    except Exception:
        return None


def configure_tailscale() -> bool:
    """Configure Tailscale Funnel or Serve in background mode."""
    if TAILSCALE_MODE in ("none", "off", "local", ""):
        log("Tailscale mode is 'none' — running local-only.")
        return True
    if not _ts_exists():
        log("Tailscale CLI not found. Install Tailscale from https://tailscale.com/download")
        return False
    cmd_name = "funnel" if TAILSCALE_MODE == "funnel" else "serve"
    cmd = [TAILSCALE_BIN, cmd_name, "--bg", str(PORT)]
    log(f"Configuring Tailscale: {' '.join(cmd)}")
    try:
        res = sp.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0:
            log(f"Tailscale {cmd_name} enabled on port {PORT}.")
            return True
        log(f"Tailscale {cmd_name} failed ({res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
        return False
    except Exception as e:
        log(f"Tailscale {cmd_name} error: {e}")
        return False


def disable_tailscale() -> None:
    if TAILSCALE_MODE in ("none", "off", "local", "") or not _ts_exists():
        return
    cmd_name = "funnel" if TAILSCALE_MODE == "funnel" else "serve"
    try:
        sp.run(
            [TAILSCALE_BIN, cmd_name, "--https=443", "off"],
            capture_output=True,
            timeout=15,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def check_local_health() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_server() -> sp.Popen:
    py = VENV_PYTHONW if VENV_PYTHONW.exists() else (VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # Auto-populate MYPC_MCP_BASE_URL from Tailscale if not explicitly set
    if not env.get("MYPC_MCP_BASE_URL"):
        ts_url = get_tailscale_url()
        if ts_url:
            env["MYPC_MCP_BASE_URL"] = ts_url
    log(f"Starting MCP server with {py} on {HOST}:{PORT}...")
    with SERVER_LOG.open("ab") as logf:
        return sp.Popen(
            [str(py), str(RUN_PY)],
            cwd=str(BASE_DIR),
            stdout=logf,
            stderr=sp.STDOUT,
            env=env,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )


def cmd_status() -> int:
    healthy = check_local_health()
    ts_url = get_tailscale_url()
    print(f"Local server (127.0.0.1:{PORT}): {'UP' if healthy else 'DOWN'}")
    print(f"Tailscale mode: {TAILSCALE_MODE}")
    if ts_url:
        print(f"Tailscale URL : {ts_url}/mcp")
    else:
        print("Tailscale URL : (not detected — run 'tailscale status')")
    print(f"Logs          : {LOG_DIR}")
    return 0 if healthy else 1


def cmd_stop() -> int:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            sp.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)
    disable_tailscale()
    print("MyPC MCP stopped.")
    return 0


def run_daemon() -> int:
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    configure_tailscale()
    proc: sp.Popen | None = None
    try:
        while True:
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    log(f"MCP server exited with code {proc.returncode}; restarting in 3s...")
                    time.sleep(3)
                proc = start_server()
            time.sleep(5)
    except KeyboardInterrupt:
        log("Supervisor interrupted; shutting down.")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
        PID_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "daemon"
    if arg == "status":
        raise SystemExit(cmd_status())
    if arg == "stop":
        raise SystemExit(cmd_stop())
    raise SystemExit(run_daemon())
