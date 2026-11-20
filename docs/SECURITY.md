# MyPC MCP — Security Guide

## Trust Model

MyPC MCP grants connected AI clients full-machine filesystem and command access **under your current Windows user account**. Treat `MYPC_MCP_API_KEY`, `MYPC_MCP_CONSENT_PIN`, `oauth_clients.json`, and `oauth_tokens.json` as private credentials.

## Tailscale Funnel vs. Tailscale Serve

| Mode | Reachability | Recommended When |
| :--- | :--- | :--- |
| `tailscale serve` | **Private** (only devices inside your own Tailnet) | Using Claude Code, Cursor, or local agents on another laptop/phone. |
| `tailscale funnel` | **Public HTTPS** (protected by OAuth PIN + API key) | Connecting cloud web AI clients (ChatGPT Web, Claude Web, Gemini Web). |

## Built-in Protections

- **No privilege escalation:** Runs as the signed-in Windows user; Windows ACLs and UAC are never bypassed.
- **Owner PIN consent:** OAuth `/authorize` requires `MYPC_MCP_CONSENT_PIN` by default (`MYPC_MCP_AUTO_APPROVE_CHATGPT=0`) with automatic ticket lockout after 5 wrong PIN attempts.
- **Redirect URI allowlist:** Prevents open-redirect token theft during OAuth DCR.
- **Optional drive restriction:** Set `MYPC_MCP_ALLOWED_DRIVES=C:,D:` in `.env` if you want to restrict accessible drives.
- **Audit logging:** Every tool call is logged to `%LOCALAPPDATA%\MyPCMcp\audit.jsonl` with secrets redacted and file contents excluded.
- **Clean `.gitignore`:** Blocks `.env`, `oauth_clients.json`, `oauth_tokens.json`, and `*.log` from ever being committed.
