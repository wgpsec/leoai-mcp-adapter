from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import uvicorn

from .client import LeoAIClient
from .config import Settings
from .server import create_app


def build_app(
    env: Mapping[str, str] | None = None,
    *,
    config_path: str | Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
):
    settings = (
        Settings.from_toml(config_path)
        if config_path is not None
        else Settings.from_env(os.environ if env is None else env)
    )
    client = LeoAIClient(settings, transport=transport)
    return create_app(settings, client)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LeoAI MCP Adapter")
    parser.add_argument("--config", help="Path to a TOML configuration file")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    app = build_app(config_path=args.config)
    settings = app.state.settings
    uvicorn.run(
        app,
        host=settings.mcp_bind_host,
        port=settings.mcp_bind_port,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
