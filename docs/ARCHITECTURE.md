# MyPC MCP — Architecture

## Overview

MyPC MCP is a Streamable HTTP MCP server (Python FastMCP 3.x) running locally on Windows (`127.0.0.1:3080`) and exposed cleanly over HTTPS via **Tailscale Funnel** or **Tailscale Serve**.

```text
AI Client (ChatGPT / Claude / Gemini / Cursor)
        │  HTTPS + OAuth 2.1 (PKCE + PIN) / Bearer API Key
        ▼
Tailscale Funnel (public) or Tailscale Serve (Tailnet-only)
        │  Local reverse proxy
        ▼
127.0.0.1:3080/mcp   ← MyPC MCP Server (Windows)
        │
        ├── Filesystem & Editing (get_roots, list_directory, read_file_range, patch_file...)
        ├── Search & Archive     (search_files, grep, file_hash, zip/unzip)
        ├── Command & Process    (exec_command, start_process, get_process_output, kill_process)
        ├── Testing & Git        (run_pytest, run_python_script, git_status/diff/log/commit)
        └── System & Chrome CDP  (system_info, disk_usage, cdp_list_pages, cdp_evaluate...)
```

## Modules

| Module | Responsibility |
| :--- | :--- |
| `server.py` | FastMCP app (`name="mypc"`), OAuth 2.1 provider, API-key middleware, `/health`, `/consent` |
| `register_all.py` | Central tool registration with audit logging, annotations, and structured errors |
| `extended_tools.py` | Core full-machine filesystem, search, archive, and editing tools |
| `exec_tools.py` / `extra_tools.py` | CLI execution, testing, Git, system info, and Chrome CDP helpers |
| `security/paths.py` | Path normalization, reserved-name rejection, zip-slip guard |
| `security/oauth_policy.py` | Redirect URI allowlist + owner PIN consent page |
| `security/audit.py` | JSONL audit log (`%LOCALAPPDATA%\MyPCMcp\audit.jsonl`) with secret redaction |
| `services/process_manager.py` | Bounded-buffer background process registry with clean shutdown |
| `start_mcp.py` | Daemon supervisor managing the local server and Tailscale Funnel/Serve |
