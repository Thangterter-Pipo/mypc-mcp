"""Core smoke tests verifying MyPC MCP imports, server name, paths, and audit redaction."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security import audit, paths  # noqa: E402
import server  # noqa: E402


def test_server_identity() -> None:
    assert server.mcp.name == "mypc"


def test_audit_redacts_secrets() -> None:
    cleaned = audit.sanitize_params({"path": "C:\\temp", "api_key": "supersecret", "token": "abc"})
    assert cleaned["path"] == "C:\\temp"
    assert cleaned["api_key"] == "<redacted>"
    assert cleaned["token"] == "<redacted>"


def test_paths_reject_nul_and_reserved() -> None:
    for bad in ("", "   ", "C:\\foo\x00bar", "C:\\CON"):
        try:
            paths.normalize(bad)
            raise AssertionError(f"Expected PathError for {bad!r}")
        except paths.PathError:
            pass
