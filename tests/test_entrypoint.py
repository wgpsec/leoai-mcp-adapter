from __future__ import annotations

import os

import httpx
import pytest

from leoai_mcp_adapter.__main__ import build_app, parse_args


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


def test_parse_args_accepts_a_toml_config_path():
    args = parse_args(["--config", "adapter.toml"])

    assert args.config == "adapter.toml"


@pytest.mark.asyncio
async def test_build_app_creates_a_runnable_health_endpoint_from_toml(tmp_path):
    password_file = tmp_path / "leoai-password"
    token_file = tmp_path / "mcp-token"
    password_file.write_text("operator-password", encoding="utf-8")
    token_file.write_text("adapter-token", encoding="utf-8")
    os.chmod(password_file, 0o600)
    os.chmod(token_file, 0o600)
    config_file = tmp_path / "adapter.toml"
    config_file.write_text(
        "\n".join(
            (
                'leoai_base_url = "http://leoai.internal:8082"',
                'leoai_username = "operator"',
                'leoai_password_file = "leoai-password"',
                'mcp_client_token_file = "mcp-token"',
                'adapter_env = "test"',
            )
        ),
        encoding="utf-8",
    )
    app = build_app(config_path=config_file)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
