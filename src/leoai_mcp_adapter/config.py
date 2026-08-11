from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

_DEFAULT_ALLOWED_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
)


class SettingsError(ValueError):
    """Raised when adapter configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class Settings:
    leoai_base_url: str
    leoai_username: str
    leoai_password: SecretStr
    mcp_client_token: SecretStr
    adapter_env: str = "production"
    leoai_tls_verify: bool = True
    leoai_connect_timeout_seconds: float = 5.0
    leoai_read_timeout_seconds: float = 30.0
    mcp_max_concurrency: int = 8
    mcp_max_response_bytes: int = 1024 * 1024
    mcp_enable_file_read: bool = False
    mcp_max_file_bytes: int = 256 * 1024
    mcp_bind_host: str = "127.0.0.1"
    mcp_bind_port: int = 8000
    mcp_allowed_hosts: tuple[str, ...] = _DEFAULT_ALLOWED_HOSTS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        base_url = _required(env, "LEOAI_BASE_URL").rstrip("/")
        adapter_env = str(env.get("ADAPTER_ENV") or "production").strip().lower()
        if adapter_env not in {"production", "development", "test"}:
            raise SettingsError("ADAPTER_ENV must be production, development, or test")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise SettingsError("LEOAI_BASE_URL must be an absolute http or https URL")
        if adapter_env == "production" and parsed_url.scheme != "https":
            raise SettingsError("production requires an https LEOAI_BASE_URL")
        username = _required(env, "LEOAI_USERNAME")
        password = _read_secret(_required(env, "LEOAI_PASSWORD_FILE"))
        client_token = _read_secret(_required(env, "MCP_CLIENT_TOKEN_FILE"))
        tls_verify = _boolean(env, "LEOAI_TLS_VERIFY", True)
        if adapter_env == "production" and not tls_verify:
            raise SettingsError("production requires LEOAI_TLS_VERIFY=true")
        allowed_hosts = (
            tuple(host.strip() for host in str(env.get("MCP_ALLOWED_HOSTS") or "").split(",") if host.strip())
            or _DEFAULT_ALLOWED_HOSTS
        )
        return cls(
            leoai_base_url=base_url,
            leoai_username=username,
            leoai_password=SecretStr(password),
            mcp_client_token=SecretStr(client_token),
            adapter_env=adapter_env,
            leoai_tls_verify=tls_verify,
            leoai_connect_timeout_seconds=_float(env, "LEOAI_CONNECT_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
            leoai_read_timeout_seconds=_float(env, "LEOAI_READ_TIMEOUT_SECONDS", 30.0, 0.1, 300.0),
            mcp_max_concurrency=_integer(env, "MCP_MAX_CONCURRENCY", 8, 1, 64),
            mcp_max_response_bytes=_integer(env, "MCP_MAX_RESPONSE_BYTES", 1024 * 1024, 1024, 16 * 1024 * 1024),
            mcp_enable_file_read=_boolean(env, "MCP_ENABLE_FILE_READ", False),
            mcp_max_file_bytes=_integer(env, "MCP_MAX_FILE_BYTES", 256 * 1024, 1, 2 * 1024 * 1024),
            mcp_bind_host=str(env.get("MCP_BIND_HOST") or "127.0.0.1").strip(),
            mcp_bind_port=_integer(env, "MCP_BIND_PORT", 8000, 1, 65535),
            mcp_allowed_hosts=allowed_hosts,
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _read_secret(raw_path: str) -> str:
    path = Path(raw_path)
    if path.stat().st_mode & 0o077:
        raise SettingsError(f"secret file must not be accessible by group or others: {raw_path}")
    value = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        raise SettingsError(f"secret file is empty: {raw_path}")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = env.get(name)
    if raw_value is None or not str(raw_value).strip():
        return default
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false")


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = env.get(name)
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        value = int(str(raw_value).strip())
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = env.get(name)
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        value = float(str(raw_value).strip())
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value
