"""MyPC MCP Server — Full-machine Windows MCP over Streamable HTTP + Tailscale.

- Python FastMCP 3.x, transport="http", bound to 127.0.0.1:3080 by default.
- Remote HTTPS access via Tailscale Funnel (public HTTPS for ChatGPT/Claude/Gemini)
  or Tailscale Serve (private Tailnet-only HTTPS).
- Auth: OAuth 2.1 (Authorization Code + PKCE + DCR + owner consent PIN) and/or
  static Bearer API key.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth.auth import (
    ClientRegistrationOptions,
    OAuthClientInformationFull,
    OAuthProvider,
)

from register_all import register_all_tools
from security import oauth_policy
from services.process_manager import PROCESS_MANAGER
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    OAuthToken,
    RefreshToken,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(os.environ.get("MYPC_MCP_ROOT", str(Path.home()))).resolve()
API_KEY = os.environ.get("MYPC_MCP_API_KEY", "")
HOST = os.environ.get("MYPC_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MYPC_MCP_PORT", "3080"))
RUNTIME_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MyPCMcp"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE_REQUEST_FILE = RUNTIME_DIR / "active_request.json"
_ACTIVE_REQUESTS = 0
_ACTIVE_REQUEST_STARTED_AT: str | None = None

# Public HTTPS base URL. Required for OAuth — the server must be reachable at
# this URL for /authorize, /token, DCR and .well-known discovery to work.
# Resolved from the Tailscale Funnel / Serve HTTPS URL.
BASE_URL = os.environ.get("MYPC_MCP_BASE_URL", f"http://{HOST}:{PORT}").rstrip("/")

# Paths skipped by list_tree (never surfaced, never editable) — keeps the
# knowledge graph focused and avoids handing out huge dirs.
EXCLUDED = {".venv", ".git", "node_modules", "__pycache__", ".pytest_cache"}

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml", ".toml",
    ".md", ".mdx", ".txt", ".rst", ".csv", ".html", ".htm", ".css", ".scss",
    ".xml", ".ini", ".cfg", ".conf", ".env", ".sh", ".bat", ".ps1", ".sql",
    ".java", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".ipynb", ".lua",
    ".rb", ".php", ".vue", ".svelte", ".lock", ".log", ".diff", ".patch",
}


# ---------------------------------------------------------------------------
# Request activity + Bearer API-key auth (ASGI middleware)
# ---------------------------------------------------------------------------

def _write_request_activity() -> None:
    """Publish MCP request activity for the external supervisor.

    The MCP server intentionally permits long-running tools. Those tools can
    occupy the asyncio loop, so /health may time out even though a valid tool is
    still running. The supervisor reads this marker and never kills legitimate
    work merely because the HTTP health probe is temporarily blocked.
    """
    try:
        if _ACTIVE_REQUESTS <= 0:
            ACTIVE_REQUEST_FILE.unlink(missing_ok=True)
            return
        payload = {
            "pid": os.getpid(),
            "active_requests": _ACTIVE_REQUESTS,
            "started_at": _ACTIVE_REQUEST_STARTED_AT,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = ACTIVE_REQUEST_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(ACTIVE_REQUEST_FILE)
    except OSError:
        # Activity reporting must never break an MCP request.
        pass


class RequestActivityMiddleware(BaseHTTPMiddleware):
    """Mark in-flight MCP requests so the watchdog preserves long tool calls."""

    async def dispatch(self, request, call_next):
        global _ACTIVE_REQUESTS, _ACTIVE_REQUEST_STARTED_AT

        if not request.url.path.startswith("/mcp"):
            return await call_next(request)

        if _ACTIVE_REQUESTS == 0:
            _ACTIVE_REQUEST_STARTED_AT = datetime.now(timezone.utc).isoformat()
        _ACTIVE_REQUESTS += 1
        _write_request_activity()
        try:
            return await call_next(request)
        finally:
            _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)
            if _ACTIVE_REQUESTS == 0:
                _ACTIVE_REQUEST_STARTED_AT = None
            _write_request_activity()


def _check_auth(request) -> bool:
    """Compare the Authorization header against MYPC_MCP_API_KEY."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return secrets.compare_digest(auth[7:].strip(), API_KEY)
    # Also accept the key directly in the header (some hosts only allow one
    # fixed header name).
    return secrets.compare_digest(auth.strip(), API_KEY)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Reject every request that does not carry the correct Bearer API key.

    Applied as the outermost middleware so not even the MCP endpoints are
    reachable without the key. The /health endpoint is exempt so tunnel /
    load-balancer probes can check liveness without leaking anything.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Public endpoints: health probes, OAuth discovery + authorize page,
        # token endpoint (client auth is handled inside OAuth), DCR register,
        # and the OIDC/well-known metadata. These must be reachable without the
        # static API key so ChatGPT can complete the OAuth flow.
        public_paths = (
            "/health",
            "/healthz",
            "/.well-known/",
            "/authorize",
            "/consent",
            "/token",
            "/register",
            "/revoke",
            "/introspect",
            "/userinfo",
            "/jwks",
            "/.well-known/openid-configuration",
        )
        if path in ("/health", "/healthz") or any(path.startswith(p) for p in public_paths):
            return await call_next(request)
        if not API_KEY:
            # No static API key configured: let FastMCP's OAuth layer handle
            # authorization (it will reject requests without a valid token).
            return await call_next(request)
        if _check_auth(request):
            return await call_next(request)
        # Not the static API key. If the request carries an OAuth access token
        # (from the completed authorization flow), FastMCP's RequireAuthMiddleware
        # will verify it — pass through. Otherwise FastMCP rejects with 401.
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            return await call_next(request)
        return JSONResponse(
            {"error": "Unauthorized. Provide the API key in the Authorization header as 'Bearer <key>', or complete the OAuth flow to obtain an access token."},
            status_code=401,
        )


# ---------------------------------------------------------------------------
# Path safety (Allows both C: and E: drives)
# ---------------------------------------------------------------------------

ALLOWED_DRIVES = {d.strip().upper().rstrip(":") + ":" for d in os.environ.get("MYPC_MCP_ALLOWED_DRIVES", "").split(",") if d.strip()}


def _is_inside_allowed(path: Path) -> bool:
    """True if path is within any of the allowed drives (C:, E:)."""
    try:
        resolved = path.resolve()
        return (not ALLOWED_DRIVES) or (resolved.drive.upper() in ALLOWED_DRIVES)
    except Exception:
        return False


def _is_inside(path: Path, root: Path) -> bool:
    """True if `path` is at or under `root` or within allowed drives."""
    return _is_inside_allowed(path)


def _format_path(path: Path) -> str:
    """Return a clean path string, relative to ROOT if possible, else absolute posix."""
    try:
        return path.relative_to(ROOT).as_posix()
    except (ValueError, Exception):
        return path.as_posix()


def _validate_relpath(relpath: str, for_write: bool = False) -> Path:
    """Resolve a user-supplied relative or absolute path against allowed drives (C:, E:)."""
    if not relpath or relpath.isspace():
        raise ValueError("path must not be empty.")

    stripped = relpath.strip()
    import re
    if re.match(r"^[A-Za-z]:[\\/]", stripped) or stripped.startswith("\\\\"):
        candidate = Path(stripped).resolve()
    elif stripped.startswith("/c/") or stripped.startswith("/C/"):
        candidate = Path("C:/" + stripped[3:]).resolve()
    elif stripped.startswith("/e/") or stripped.startswith("/E/"):
        candidate = Path("E:/" + stripped[3:]).resolve()
    else:
        rel = stripped.replace("\\", "/").lstrip("/")
        candidate = (ROOT / rel).resolve()

    if not _is_inside_allowed(candidate):
        raise ValueError(
            f"Path '{relpath}' is outside allowed drives ({', '.join(sorted(ALLOWED_DRIVES))}). "
            f"Supported drives: C:, E:"
        )
    if for_write and candidate.as_posix() in [f"{d.lower()}/" for d in ALLOWED_DRIVES]:
        raise ValueError("Cannot write to the drive root directory directly.")
    return candidate


def _suggest(relpath: str) -> str | None:
    """Best-effort suggestion when a path is missing."""
    try:
        parent = _validate_relpath(str(Path(relpath).parent))
    except ValueError:
        return None
    try:
        children = [p.name for p in parent.iterdir() if not p.name.startswith(".")]
    except OSError:
        return None
    for name in sorted(children):
        if name.lower().startswith(Path(relpath).name.lower()):
            return _format_path(parent / name)
    return None


def _render_error(err: ValueError) -> dict:
    return {
        "ok": False,
        "error": str(err),
        "root": str(ROOT),
        "allowed_drives": list(sorted(ALLOWED_DRIVES)),
        "hint": "Both C: and E: drives are supported. You can pass absolute paths (e.g. C:/... or E:/...) or relative paths.",
    }


def _file_entry(path: Path) -> dict:
    rel = _format_path(path)
    is_dir = path.is_dir()
    return {
        "name": path.name,
        "path": rel,
        "type": "dir" if is_dir else "file",
        "size": 0 if is_dir else (path.stat().st_size if path.exists() else 0),
        "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat() if path.exists() else "",
    }


def _read_text_or_binary(path: Path, max_text_chars: int) -> dict:
    """Read a file, returning text for known-text types else base64 blob."""
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS or suffix == "":
        try:
            data = path.read_text(encoding="utf-8")
            truncated = len(data) > max_text_chars
            if truncated:
                data = data[:max_text_chars] + f"\n...[truncated {len(data)} -> {max_text_chars} chars]"
            return {"encoding": "utf-8", "content": data, "truncated": truncated}
        except UnicodeDecodeError:
            pass  # fall through to binary
    size = path.stat().st_size
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"encoding": "base64", "content": b64, "truncated": False, "size": size}


# ---------------------------------------------------------------------------
# OAuth client store (persistent so a registered ChatGPT client survives restarts)
# ---------------------------------------------------------------------------

class PersistentOAuthProvider(OAuthProvider):
    """OAuthProvider with a JSON-file backed client store + full auth-code flow.

    FastMCP's OAuthProvider leaves the SDK's abstract methods (get_client,
    register_client, authorize, load/exchange authorization codes & tokens) as
    no-op stubs, so without this subclass every registered client would be
    "not found" on /authorize and /authorize would redirect to a None URL.

    This subclass implements the complete OAuth 2.1 authorization-code + PKCE
    flow:
      - Clients persisted to oauth_clients.json (register once, reuse forever).
      - /authorize generates a short-lived auth code (160-bit) bound to the
        PKCE challenge + redirect_uri, stores it, and redirects straight back
        to the client's redirect_uri (no third-party consent page — this is a
        single-owner filesystem, so consent is implicit).
      - /token exchanges the code for a Bearer access token + refresh token,
        verifying the PKCE code_verifier (done by the SDK handler) and
        redirect_uri.
      - Access/refresh tokens are stored in-memory with expiry; access tokens
        are also returned to verify_token for /mcp authorization.
    """

    def __init__(self, *args, store_path: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._store_path = Path(store_path) if store_path else Path(__file__).parent / "oauth_clients.json"
        self._tokens_path = Path(__file__).parent / "oauth_tokens.json"
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        if self._store_path.exists():
            try:
                import json as _json

                raw = _json.loads(self._store_path.read_text(encoding="utf-8"))
                for cid, data in raw.items():
                    self._clients[cid] = OAuthClientInformationFull.model_validate(data)
            except Exception:
                self._clients = {}
        if self._tokens_path.exists():
            try:
                import json as _json

                raw_tokens = _json.loads(self._tokens_path.read_text(encoding="utf-8"))
                now_ts = datetime.now(timezone.utc).timestamp()
                for tok, data in raw_tokens.get("access_tokens", {}).items():
                    exp = data.get("expires_at")
                    if exp is None or exp > now_ts:
                        self._access_tokens[tok] = AccessToken.model_validate(data)
                for tok, data in raw_tokens.get("refresh_tokens", {}).items():
                    exp = data.get("expires_at")
                    if exp is None or exp > now_ts:
                        self._refresh_tokens[tok] = RefreshToken.model_validate(data)
            except Exception:
                pass

    def _save_tokens(self) -> None:
        import json as _json

        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            data = {
                "access_tokens": {
                    tok: item.model_dump(mode="json")
                    for tok, item in self._access_tokens.items()
                    if item.expires_at is None or item.expires_at > now_ts
                },
                "refresh_tokens": {
                    tok: item.model_dump(mode="json")
                    for tok, item in self._refresh_tokens.items()
                    if item.expires_at is None or item.expires_at > now_ts
                },
            }
            self._tokens_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _save(self) -> None:
        import json as _json

        self._store_path.write_text(
            _json.dumps({cid: c.model_dump(mode="json") for cid, c in self._clients.items()}, indent=2),
            encoding="utf-8",
        )

    def _new_token(self, length: int = 48) -> str:
        """Cryptographically-random URL-safe token string (>=160 bits)."""
        return secrets.token_urlsafe(length)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        oauth_policy.assert_client_redirects(
            [str(u) for u in (client_info.redirect_uris or [])]
        )
        # Guarantee the client can request every scope we support. Some clients
        # (ChatGPT) request scopes individually during /authorize
        # (scope=filesystem+read+write) and validate_scope rejects anything the
        # client was not registered with. If the client omitted scopes or
        # registered a subset, fill in the full set so authorization succeeds.
        if not client_info.scope:
            client_info.scope = "filesystem read write"
        else:
            current = set(client_info.scope.split())
            full = {"filesystem", "read", "write"}
            if not full.issubset(current):
                client_info.scope = " ".join(sorted(full | current))
        self._clients[client_info.client_id] = client_info
        self._save()

    async def authorize(self, client: OAuthClientInformationFull, params) -> str:
        """Authorize the client.
        
        If redirect_uri is an allowlisted ChatGPT callback and auto-approve is
        enabled (MYPC_MCP_AUTO_APPROVE_CHATGPT != '0'), automatically issue
        the code and redirect directly to ChatGPT without parking for manual PIN.
        """
        redirect_uri = str(getattr(params, "redirect_uri", "") or "")
        auto_approve = os.environ.get("MYPC_MCP_AUTO_APPROVE_CHATGPT", "0").strip() == "1"
        is_chatgpt_redirect = any(
            redirect_uri.startswith(p)
            for p in ("https://chatgpt.com/", "https://chat.openai.com/")
        )
        if auto_approve and is_chatgpt_redirect:
            return await self.issue_code(client, params)

        try:
            ticket = oauth_policy.park_consent(
                client_id=client.client_id,
                client_name=getattr(client, "client_name", None) or "ChatGPT",
                params=params,
            )
        except ValueError:
            return f"{BASE_URL}/consent?error=redirect_not_allowed"
        return f"{BASE_URL}/consent?ticket={ticket}"

    async def issue_code(self, client: OAuthClientInformationFull, params) -> str:
        """Issue the authorization code and return the client redirect URL."""
        import urllib.parse as _up

        code = self._new_token(32)
        now = datetime.now(timezone.utc).timestamp()
        self._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=now + 600,  # 10 min
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=params.resource,  # no real user; tie to the resource URL
        )
        query = _up.urlencode(
            {"code": code, **({"state": params.state} if params.state else {})}
        )
        # params.redirect_uri is an AnyUrl; append query preserving the fragment.
        url = str(params.redirect_uri)
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{query}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._auth_codes.get(authorization_code)
        if code is None:
            return None
        if datetime.now(timezone.utc).timestamp() > code.expires_at:
            self._auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Exchange an auth code for access + refresh tokens (single-use code)."""
        self._auth_codes.pop(authorization_code.code, None)  # one-time use

        scopes = authorization_code.scopes or ["filesystem"]
        access = self._new_token(32)
        refresh = self._new_token(40)
        now = datetime.now(timezone.utc).timestamp()

        self._access_tokens[access] = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(now) + 3600,  # 1 hour
            resource=authorization_code.resource,
            subject=authorization_code.subject,
        )
        self._refresh_tokens[refresh] = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(now) + 7 * 86400,  # 7 days
            subject=authorization_code.subject,
        )
        self._save_tokens()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        tok = self._refresh_tokens.get(refresh_token)
        if tok is None:
            return None
        if datetime.now(timezone.utc).timestamp() > tok.expires_at:
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return tok

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate the refresh token and issue a fresh access token."""
        self._refresh_tokens.pop(refresh_token.token, None)
        access = self._new_token(32)
        refresh = self._new_token(40)
        now = datetime.now(timezone.utc).timestamp()
        final_scopes = scopes or refresh_token.scopes or ["filesystem"]

        self._access_tokens[access] = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=final_scopes,
            expires_at=int(now) + 3600,
            subject=refresh_token.subject,
        )
        self._refresh_tokens[refresh] = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=final_scopes,
            expires_at=int(now) + 7 * 86400,
            subject=refresh_token.subject,
        )
        self._save_tokens()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(final_scopes),
            refresh_token=refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        matches_key = bool(API_KEY and secrets.compare_digest(token, API_KEY))
        # Static API key → treat as a valid access token with full scopes.
        # This preserves backward-compatibility for clients that authenticate
        # with the raw API key instead of going through the OAuth flow.
        if matches_key:
            return AccessToken(
                token=token,
                client_id="static-api-key",
                scopes=["filesystem", "read", "write"],
                expires_at=None,  # never expires
                resource=str(self._resource_url) if self._resource_url else None,
            )
        tok = self._access_tokens.get(token)
        if tok is None:
            return None
        if datetime.now(timezone.utc).timestamp() > tok.expires_at:
            self._access_tokens.pop(token, None)
            return None
        return tok


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="mypc",
    instructions=(
        "You are connected to a Windows computer via MyPC MCP (Streamable HTTP). "
        "Start with get_roots() to discover available drives, then use list_directory / "
        "list_tree, read_file_range, patch_file, exec_command, process management, Git, "
        "and Chrome CDP tools under the current Windows user's permissions. "
        "Use absolute Windows paths (e.g. C:\\Users\\...). "
        "Always read files before editing and confirm destructive operations."
    ),
    # OAuth Authorization Server: enables ChatGPT's OAuth connector flow.
    # - Dynamic Client Registration (DCR) so ChatGPT can register a client id.
    # - Authorization Code + PKCE flow with /authorize, /token, discovery.
    # base_url must be the public HTTPS origin (Tailscale Funnel / Serve).
    auth=PersistentOAuthProvider(
        base_url=BASE_URL,
        resource_base_url=BASE_URL,
        service_documentation_url=f"{BASE_URL}/health",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["filesystem", "read", "write"],
            default_scopes=["filesystem"],
        ),
    ),
)

# Register the full-machine tool suite (filesystem, editing, search, exec,
# processes, system, git, archive, CDP...).
register_all_tools(mcp)

# Duplicate workspace tools removed to eliminate warnings and speed up LLM discovery.

# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not API_KEY:
        print(
            "[warn] MYPC_MCP_API_KEY is not set. The server will reject all "
            "requests. Set it to a strong random value (e.g. secrets.token_urlsafe(32)).",
            flush=True,
        )

    if not ROOT.is_dir():
        raise SystemExit(f"MYPC_MCP_ROOT '{ROOT}' does not exist.")

    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
    from starlette.routing import Mount, Route

    # FastMCP app — mounts its MCP endpoints under "/mcp" plus the OAuth
    # endpoints from the auth provider (/.well-known, /authorize, /token,
    # /register/DCR...). host_origin_protection stays off so the Tailscale
    # reverse proxy Host header is accepted.
    mcp_app = mcp.http_app(transport="http", host_origin_protection=False)

    async def health(request):
        return JSONResponse({
            "status": "ok",
            "active_requests": _ACTIVE_REQUESTS,
        })

    async def oauth_discovery(request):
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        proto = request.headers.get("x-forwarded-proto") or "https"
        base = f"{proto}://{host}".rstrip("/") if host else BASE_URL
        return JSONResponse({
            "issuer": f"{base}/",
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "scopes_supported": ["filesystem", "read", "write"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "service_documentation": f"{base}/health",
            "code_challenge_methods_supported": ["S256"],
        })

    async def consent_get(request):
        err = request.query_params.get("error")
        if err and not request.query_params.get("ticket"):
            return HTMLResponse(oauth_policy.error_page("OAuth denied: redirect_uri is not in the allowlist."), 400)
        ticket = request.query_params.get("ticket", "")
        payload = oauth_policy.take_ticket(ticket)
        if payload is None:
            return HTMLResponse(oauth_policy.error_page("Ticket hết hạn hoặc không hợp lệ."), 400)
        return HTMLResponse(oauth_policy.consent_page(
            ticket=ticket,
            client_name=payload["client_name"],
            redirect_uri=payload["redirect_uri"],
        ))

    async def consent_post(request):
        import urllib.parse as _up

        raw = (await request.body()).decode("utf-8", errors="replace")
        form = dict(_up.parse_qsl(raw))
        ticket = str(form.get("ticket") or "")
        decision = str(form.get("decision") or "")
        payload = oauth_policy.take_ticket(ticket)
        if payload is None:
            return HTMLResponse(oauth_policy.error_page("Ticket hết hạn hoặc không hợp lệ."), 400)

        def _client_error_redirect(code: str) -> RedirectResponse:
            import urllib.parse as _up
            url = payload["redirect_uri"]
            q = {"error": code}
            if payload.get("state"):
                q["state"] = payload["state"]
            sep = "&" if "?" in url else "?"
            return RedirectResponse(url + sep + _up.urlencode(q), status_code=302)

        if decision != "allow":
            oauth_policy.consume_ticket(ticket)
            return _client_error_redirect("access_denied")

        if not oauth_policy.pin_ok(str(form.get("pin") or "")):
            burned = oauth_policy.record_pin_failure(ticket)
            msg = "Sai PIN." + (" Ticket đã hủy." if burned else "")
            remaining = oauth_policy.take_ticket(ticket)
            if remaining is None:
                return HTMLResponse(oauth_policy.error_page(msg), 401)
            return HTMLResponse(oauth_policy.consent_page(
                ticket=ticket,
                client_name=remaining["client_name"],
                redirect_uri=remaining["redirect_uri"],
                error=msg,
            ), 401)

        payload = oauth_policy.consume_ticket(ticket)
        if payload is None:
            return HTMLResponse(oauth_policy.error_page("Ticket hết hạn."), 400)
        client = await mcp.auth.get_client(payload["client_id"])
        if client is None:
            return HTMLResponse(oauth_policy.error_page("OAuth client không còn tồn tại."), 400)
        url = await mcp.auth.issue_code(client, payload["params"])
        return RedirectResponse(url, status_code=302)

    from fastmcp.server.lifespan import Lifespan

    # Kill any long-running processes (Chrome CDP, dev servers) on shutdown so
    # they never leak after the server stops.
    @asynccontextmanager
    async def _lifespan(app):
        async with mcp_app.lifespan(app):
            try:
                yield
            finally:
                PROCESS_MANAGER.shutdown()

    # Parent app: serves /health and carries the FastMCP lifespan. Activity
    # tracking is outermost so the supervisor can distinguish a busy server from
    # a wedged one; API-key enforcement remains in front of the MCP app.
    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/.well-known/oauth-authorization-server", oauth_discovery),
            Route("/consent", consent_get, methods=["GET"]),
            Route("/consent", consent_post, methods=["POST"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[
            Middleware(RequestActivityMiddleware),
            Middleware(APIKeyMiddleware),
        ],
        lifespan=_lifespan,
    )
    # Keep-alive balance: request handling itself is unbounded, this only
    # affects idle connection reuse between MCP messages. A too-long idle
    # keep-alive (120s) let reverse proxies / Tailscale hold a socket open past their
    # own timeout, then reset it abruptly — flooding asyncio with
    # ConnectionResetError [WinError 10054] callbacks that, under CPU-heavy
    # builds, backlogged the event loop and wedged the server. 20s recycles
    # idle connections cleanly before proxies drop them.
    uvicorn.run(
        app, host=HOST, port=PORT, log_level="info",
        timeout_keep_alive=int(os.environ.get("UVICORN_KEEP_ALIVE", "20")),
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
