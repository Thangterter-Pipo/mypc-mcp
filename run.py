"""Load .env next to this script so the server picks up MYPC_MCP_* variables."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.pop("PYTHONPATH", None)

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from server import main  # noqa: E402

if __name__ == "__main__":
    main()
