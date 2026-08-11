from __future__ import annotations

import os

import httpx
import pytest

from leoai_mcp_adapter.__main__ import build_app


@pytest.mark.asyncio
async def test_build_app_creates_a_runnable_health_endpoint_from_environment(tmp_path):
    password_file = tmp_path / "leoai-password"
    token_file = tmp_path / "mcp-token"
    password_file.write_text("operator-password", encoding="utf-8")
    token_file.write_text("adapter-token", encoding="utf-8")
    password_file.chmod(0o600)
    token_file.chmod(0o600)

    app = build_app(
        {
            "LEOAI_BASE_URL": "https://leoai.internal",
            "LEOAI_USERNAME": "operator",
            "LEOAI_PASSWORD_FILE": os.fspath(password_file),
            "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
        },
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://adapter") as client:
        response = await client.get("/healthz")
    await app.state.leoai.aclose()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
