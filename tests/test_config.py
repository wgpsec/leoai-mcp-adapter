from __future__ import annotations

import os

import pytest

from leoai_mcp_adapter.config import Settings, SettingsError


def _environment(tmp_path) -> dict[str, str]:
    password_file = tmp_path / "leoai-password"
    token_file = tmp_path / "mcp-token"
    password_file.write_text("operator-password", encoding="utf-8")
    token_file.write_text("adapter-token", encoding="utf-8")
    password_file.chmod(0o600)
    token_file.chmod(0o600)
    return {
        "LEOAI_BASE_URL": "https://leoai.internal",
        "LEOAI_USERNAME": "operator",
        "LEOAI_PASSWORD_FILE": os.fspath(password_file),
        "MCP_CLIENT_TOKEN_FILE": os.fspath(token_file),
    }


def test_protocol_profile_defaults_to_2x_and_accepts_explicit_generations(tmp_path):
    environment = _environment(tmp_path)

    assert Settings.from_env(environment).leoai_protocol_profile == "2x"
    assert Settings.from_env(environment | {"LEOAI_PROTOCOL_PROFILE": "1x"}).leoai_protocol_profile == "1x"
    assert Settings.from_env(environment | {"LEOAI_PROTOCOL_PROFILE": "2x"}).leoai_protocol_profile == "2x"


def test_protocol_profile_rejects_unknown_generations(tmp_path):
    with pytest.raises(SettingsError, match="LEOAI_PROTOCOL_PROFILE must be one of: 1x, 2x"):
        Settings.from_env(_environment(tmp_path) | {"LEOAI_PROTOCOL_PROFILE": "auto"})
