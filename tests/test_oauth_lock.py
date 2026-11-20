"""Unit tests for MyPC MCP OAuth redirect allowlist + consent PIN."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ["MYPC_MCP_CONSENT_PIN"] = "unit-test-pin"
os.environ.pop("MYPC_MCP_OAUTH_REDIRECT_PREFIXES", None)
os.environ.pop("MYPC_MCP_ALLOW_LOCAL_OAUTH", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from security import oauth_policy as pol  # noqa: E402


def test_redirect_allowlist() -> None:
    assert pol.allowed_redirect("https://chatgpt.com/connector/oauth/callback")
    assert pol.allowed_redirect("https://chatgpt.com/connector/oauth/abc123")
    assert pol.allowed_redirect("https://claude.ai/api/mcp/auth_callback")
    assert pol.allowed_redirect("https://oauth-redirect.googleusercontent.com/r/project")
    assert not pol.allowed_redirect("https://example.com/cb")
    assert not pol.allowed_redirect("javascript:alert(1)")
    assert not pol.allowed_redirect("http://chatgpt.com/connector/oauth/callback")
    assert not pol.allowed_redirect("https://chatgpt.com/backend-api/foo")


def test_consent_pin_and_lockout() -> None:
    params = SimpleNamespace(
        state="st",
        scopes=["filesystem"],
        code_challenge="abc",
        redirect_uri="https://chatgpt.com/connector/oauth/callback",
        redirect_uri_provided_explicitly=True,
        resource="https://demo.tailnet.ts.net",
    )
    ticket = pol.park_consent(client_id="cid", client_name="ChatGPT", params=params)
    assert ticket and pol.take_ticket(ticket) is not None
    assert not pol.pin_ok("wrong-pin")
    assert pol.pin_ok("unit-test-pin")

    burned = False
    for _ in range(pol.MAX_PIN_FAILS):
        burned = pol.record_pin_failure(ticket)
    assert burned and pol.take_ticket(ticket) is None


def test_consent_html_escapes_xss() -> None:
    page = pol.consent_page(
        ticket="t",
        client_name="<script>alert(1)</script>",
        redirect_uri="https://chatgpt.com/connector/oauth/callback",
    )
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
