from __future__ import annotations

import os
from collections.abc import Mapping

import httpx
import uvicorn

from .client import LeoAIClient
from .config import Settings
from .server import create_app


def build_app(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
):
    settings = Settings.from_env(os.environ if env is None else env)
    client = LeoAIClient(settings, transport=transport)
    return create_app(settings, client)


def main() -> None:
    app = build_app()
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
