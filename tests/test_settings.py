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
    initial_password_file = _secret_file(tmp_path, "leoai-initial-password", "54ikun\n")
    token_file = _secret_file(tmp_path, "mcp-token", "adapter-token\n")

    settings = Settings.from_env(
        {
            "LEOAI_BASE_URL": "https://leoai.internal/",
            "LEOAI_USERNAME": "operator",
            "LEOAI_PASSWORD_FILE": os.fspath(password_file),
            "LEOAI_INITIAL_PASSWORD_FILE": os.fspath(initial_password_file),
            "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
        }
    )

    assert settings.leoai_base_url == "https://leoai.internal"
    assert settings.leoai_username == "operator"
    assert settings.leoai_password.get_secret_value() == "correct horse"
    assert settings.leoai_initial_password is not None
    assert settings.leoai_initial_password.get_secret_value() == "54ikun"
    assert settings.mcp_client_token.get_secret_value() == "adapter-token"
    assert settings.mcp_tool_profile == "observe"
    assert settings.mcp_enable_onboarding is False
    assert settings.mcp_max_file_write_bytes == 256 * 1024


def test_settings_loads_operate_profile_and_bounded_file_write_limit(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")

    settings = Settings.from_env(
        {
            "LEOAI_BASE_URL": "https://leoai.internal",
            "LEOAI_USERNAME": "operator",
            "LEOAI_PASSWORD_FILE": os.fspath(password_file),
            "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
            "MCP_TOOL_PROFILE": "operate",
            "MCP_MAX_FILE_WRITE_BYTES": "131072",
            "MCP_ALLOWED_PLUGIN_IDS": "plugin-safe,plugin-report,plugin-safe",
        }
    )

    assert settings.mcp_tool_profile == "operate"
    assert settings.mcp_max_file_write_bytes == 131072
    assert settings.mcp_allowed_plugin_ids == ("plugin-safe", "plugin-report")


def test_settings_rejects_invalid_plugin_allowlist_ids(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")

    with pytest.raises(SettingsError, match="MCP_ALLOWED_PLUGIN_IDS"):
        Settings.from_env(
            {
                "LEOAI_BASE_URL": "https://leoai.internal",
                "LEOAI_USERNAME": "operator",
                "LEOAI_PASSWORD_FILE": os.fspath(password_file),
                "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
                "MCP_ALLOWED_PLUGIN_IDS": "plugin-safe,../../other",
            }
        )


def test_settings_rejects_unknown_tool_profile(tmp_path):
    password_file = _secret_file(tmp_path, "leoai-password", "secret")
    token_file = _secret_file(tmp_path, "mcp-token", "token")

    with pytest.raises(SettingsError, match="MCP_TOOL_PROFILE"):
        Settings.from_env(
            {
                "LEOAI_BASE_URL": "https://leoai.internal",
                "LEOAI_USERNAME": "operator",
                "LEOAI_PASSWORD_FILE": os.fspath(password_file),
                "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
                "MCP_TOOL_PROFILE": "everything",
            }
        )


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
            "MCP_ENABLE_ONBOARDING": "true",
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
    assert settings.mcp_enable_onboarding is True
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


def test_settings_loads_flat_toml_and_resolves_secret_paths_from_config_directory(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    _secret_file(secrets, "leoai-password", "operator-password")
    _secret_file(secrets, "leoai-initial-password", "54ikun")
    _secret_file(secrets, "mcp-token", "adapter-token")
    config_file = tmp_path / "adapter.toml"
    config_file.write_text(
        """
leoai_base_url = "http://leoai.internal:8082"
leoai_username = "operator"
leoai_password_file = "secrets/leoai-password"
leoai_initial_password_file = "secrets/leoai-initial-password"
mcp_client_token_file = "secrets/mcp-token"
adapter_env = "development"
leoai_protocol_profile = "2x"
mcp_tool_profile = "operate"
mcp_enable_file_read = true
mcp_enable_onboarding = true
mcp_bind_host = "0.0.0.0"
mcp_bind_port = 18080
mcp_allowed_hosts = ["localhost:*", "host.docker.internal:*"]
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.from_toml(config_file)

    assert settings.leoai_base_url == "http://leoai.internal:8082"
    assert settings.leoai_username == "operator"
    assert settings.leoai_password.get_secret_value() == "operator-password"
    assert settings.leoai_initial_password is not None
    assert settings.leoai_initial_password.get_secret_value() == "54ikun"
    assert settings.mcp_client_token.get_secret_value() == "adapter-token"
    assert settings.adapter_env == "development"
    assert settings.leoai_protocol_profile == "2x"
    assert settings.mcp_tool_profile == "operate"
    assert settings.mcp_enable_file_read is True
    assert settings.mcp_enable_onboarding is True
    assert settings.mcp_bind_host == "0.0.0.0"
    assert settings.mcp_bind_port == 18080
    assert settings.mcp_allowed_hosts == ("localhost:*", "host.docker.internal:*")


def test_settings_rejects_unknown_toml_fields(tmp_path):
    config_file = tmp_path / "adapter.toml"
    config_file.write_text('leoai_base_url = "https://leoai.internal"\nunknown = true\n', encoding="utf-8")

    with pytest.raises(SettingsError, match="unknown configuration field: unknown"):
        Settings.from_toml(config_file)


def test_settings_loads_inline_secrets_from_private_toml(tmp_path):
    config_file = tmp_path / "adapter.toml"
    config_file.write_text(
        """
leoai_base_url = "http://leoai.internal:8082"
leoai_username = "operator"
leoai_password = "operator-password"
mcp_client_token = "adapter-token"
adapter_env = "development"
""".strip(),
        encoding="utf-8",
    )
    os.chmod(config_file, 0o600)

    settings = Settings.from_toml(config_file)

    assert settings.leoai_password.get_secret_value() == "operator-password"
    assert settings.leoai_initial_password is not None
    assert settings.leoai_initial_password.get_secret_value() == "54ikun"
    assert settings.mcp_client_token.get_secret_value() == "adapter-token"


def test_settings_rejects_inline_secrets_in_readable_toml(tmp_path):
    config_file = tmp_path / "adapter.toml"
    config_file.write_text(
        'leoai_password = "operator-password"\nmcp_client_token = "adapter-token"\n',
        encoding="utf-8",
    )
    os.chmod(config_file, 0o644)

    with pytest.raises(SettingsError, match="inline secrets must not be accessible"):
        Settings.from_toml(config_file)


def test_settings_rejects_inline_and_file_secret_for_same_credential(tmp_path):
    config_file = tmp_path / "adapter.toml"
    config_file.write_text(
        'leoai_password = "operator-password"\nleoai_password_file = "password-file"\n',
        encoding="utf-8",
    )
    os.chmod(config_file, 0o600)

    with pytest.raises(SettingsError, match="configure only one of leoai_password or leoai_password_file"):
        Settings.from_toml(config_file)


def test_settings_can_explicitly_disable_dns_rebinding_protection(tmp_path):
    config_file = tmp_path / "adapter.toml"
    config_file.write_text(
        """
leoai_base_url = "http://leoai.internal:8082"
leoai_username = "operator"
leoai_password = "operator-password"
mcp_client_token = "adapter-token"
adapter_env = "development"
mcp_dns_rebinding_protection = false
""".strip(),
        encoding="utf-8",
    )
    os.chmod(config_file, 0o600)

    settings = Settings.from_toml(config_file)

    assert settings.mcp_dns_rebinding_protection is False
