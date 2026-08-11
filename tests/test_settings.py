from __future__ import annotations

import os

import pytest

from leoai_mcp_adapter.config import Settings, SettingsError


def _secret_file(tmp_path, name: str, value: str):
    path = tmp_path / name
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_settings_loads_operator_supplied_credentials_from_secret_files(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "correct horse\n")
    token_file = _secret_file(tmp_path, "mcp-token", "adapter-token\n")

    settings = Settings.from_env(
        {
            "LEOAI_BASE_URL": "https://leoai.internal/",
            "LEOAI_USERNAME": "operator",
            "LEOAI_PASSWORD_FILE": os.fspath(password_file),
            "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
        }
    )

    assert settings.leoai_base_url == "https://leoai.internal"
    assert settings.leoai_username == "operator"
    assert settings.leoai_password.get_secret_value() == "correct horse"
    assert settings.mcp_client_token.get_secret_value() == "adapter-token"


def test_settings_rejects_group_readable_secret_files(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")
    password_file.chmod(0o640)

    with pytest.raises(SettingsError, match="must not be accessible by group or others"):
        Settings.from_env(
            {
                "LEOAI_BASE_URL": "https://leoai.internal",
                "LEOAI_USERNAME": "operator",
                "LEOAI_PASSWORD_FILE": os.fspath(password_file),
                "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
            }
        )


def test_production_settings_reject_plain_http_leoai(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")

    with pytest.raises(SettingsError, match="production requires an https LEOAI_BASE_URL"):
        Settings.from_env(
            {
                "ADAPTER_ENV": "production",
                "LEOAI_BASE_URL": "http://leoai.internal",
                "LEOAI_USERNAME": "operator",
                "LEOAI_PASSWORD_FILE": os.fspath(password_file),
                "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
            }
        )


def test_settings_loads_bounded_network_and_tool_limits(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")

    settings = Settings.from_env(
        {
            "LEOAI_BASE_URL": "https://leoai.internal",
            "LEOAI_USERNAME": "operator",
            "LEOAI_PASSWORD_FILE": os.fspath(password_file),
            "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
            "LEOAI_CONNECT_TIMEOUT_SECONDS": "3.5",
            "LEOAI_READ_TIMEOUT_SECONDS": "20",
            "MCP_MAX_CONCURRENCY": "4",
            "MCP_MAX_RESPONSE_BYTES": "524288",
            "MCP_ENABLE_FILE_READ": "true",
            "MCP_MAX_FILE_BYTES": "131072",
            "MCP_BIND_HOST": "0.0.0.0",
            "MCP_BIND_PORT": "9010",
            "MCP_ALLOWED_HOSTS": "adapter.internal,adapter.internal:443",
        }
    )

    assert settings.leoai_connect_timeout_seconds == 3.5
    assert settings.leoai_read_timeout_seconds == 20
    assert settings.mcp_max_concurrency == 4
    assert settings.mcp_max_response_bytes == 524288
    assert settings.mcp_enable_file_read is True
    assert settings.mcp_max_file_bytes == 131072
    assert settings.mcp_bind_host == "0.0.0.0"
    assert settings.mcp_bind_port == 9010
    assert settings.mcp_allowed_hosts == ("adapter.internal", "adapter.internal:443")


def test_production_settings_reject_disabled_tls_verification(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")

    with pytest.raises(SettingsError, match="production requires LEOAI_TLS_VERIFY=true"):
        Settings.from_env(
            {
                "LEOAI_BASE_URL": "https://leoai.internal",
                "LEOAI_USERNAME": "operator",
                "LEOAI_PASSWORD_FILE": os.fspath(password_file),
                "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
                "LEOAI_TLS_VERIFY": "false",
            }
        )
