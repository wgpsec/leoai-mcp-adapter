from __future__ import annotations

import re
import tomllib
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
_DEFAULT_LEOAI_INITIAL_PASSWORD = "54ikun"

_TOML_TO_ENV = {
    "leoai_base_url": "LEOAI_BASE_URL",
    "leoai_username": "LEOAI_USERNAME",
    "leoai_password_file": "LEOAI_PASSWORD_FILE",
    "leoai_initial_password_file": "LEOAI_INITIAL_PASSWORD_FILE",
    "mcp_client_token_file": "MCP_CLIENT_TOKEN_FILE",
    "adapter_env": "ADAPTER_ENV",
    "leoai_tls_verify": "LEOAI_TLS_VERIFY",
    "leoai_connect_timeout_seconds": "LEOAI_CONNECT_TIMEOUT_SECONDS",
    "leoai_read_timeout_seconds": "LEOAI_READ_TIMEOUT_SECONDS",
    "leoai_protocol_profile": "LEOAI_PROTOCOL_PROFILE",
    "mcp_max_concurrency": "MCP_MAX_CONCURRENCY",
    "mcp_max_response_bytes": "MCP_MAX_RESPONSE_BYTES",
    "mcp_tool_profile": "MCP_TOOL_PROFILE",
    "mcp_enable_file_read": "MCP_ENABLE_FILE_READ",
    "mcp_max_file_bytes": "MCP_MAX_FILE_BYTES",
    "mcp_max_file_write_bytes": "MCP_MAX_FILE_WRITE_BYTES",
    "mcp_allowed_plugin_ids": "MCP_ALLOWED_PLUGIN_IDS",
    "mcp_bind_host": "MCP_BIND_HOST",
    "mcp_bind_port": "MCP_BIND_PORT",
    "mcp_dns_rebinding_protection": "MCP_DNS_REBINDING_PROTECTION",
    "mcp_allowed_hosts": "MCP_ALLOWED_HOSTS",
}
_TOML_PATH_FIELDS = {"leoai_password_file", "leoai_initial_password_file", "mcp_client_token_file"}
_TOML_LIST_FIELDS = {"mcp_allowed_plugin_ids", "mcp_allowed_hosts"}
_TOML_INLINE_SECRET_FIELDS = {"leoai_password", "leoai_initial_password", "mcp_client_token"}


class SettingsError(ValueError):
    """Raised when adapter configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class Settings:
    leoai_base_url: str
    leoai_username: str
    leoai_password: SecretStr
    mcp_client_token: SecretStr
    leoai_initial_password: SecretStr | None = None
    adapter_env: str = "production"
    leoai_tls_verify: bool = True
    leoai_connect_timeout_seconds: float = 5.0
    leoai_read_timeout_seconds: float = 30.0
    leoai_protocol_profile: str = "2x"
    mcp_max_concurrency: int = 8
    mcp_max_response_bytes: int = 1024 * 1024
    mcp_tool_profile: str = "observe"
    mcp_enable_file_read: bool = False
    mcp_max_file_bytes: int = 256 * 1024
    mcp_max_file_write_bytes: int = 256 * 1024
    mcp_allowed_plugin_ids: tuple[str, ...] = ()
    mcp_bind_host: str = "127.0.0.1"
    mcp_bind_port: int = 8000
    mcp_dns_rebinding_protection: bool = True
    mcp_allowed_hosts: tuple[str, ...] = _DEFAULT_ALLOWED_HOSTS

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        leoai_password: str | None = None,
        leoai_initial_password: str | None = None,
        mcp_client_token: str | None = None,
    ) -> Settings:
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
        password = leoai_password or _read_secret(_required(env, "LEOAI_PASSWORD_FILE"))
        initial_password = (
            leoai_initial_password
            or _read_optional_secret(env, "LEOAI_INITIAL_PASSWORD_FILE")
            or _DEFAULT_LEOAI_INITIAL_PASSWORD
        )
        client_token = mcp_client_token or _read_secret(_required(env, "MCP_CLIENT_TOKEN_FILE"))
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
            leoai_initial_password=SecretStr(initial_password) if initial_password else None,
            mcp_client_token=SecretStr(client_token),
            adapter_env=adapter_env,
            leoai_tls_verify=tls_verify,
            leoai_connect_timeout_seconds=_float(env, "LEOAI_CONNECT_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
            leoai_read_timeout_seconds=_float(env, "LEOAI_READ_TIMEOUT_SECONDS", 30.0, 0.1, 300.0),
            leoai_protocol_profile=_choice(env, "LEOAI_PROTOCOL_PROFILE", "2x", {"1x", "2x"}),
            mcp_max_concurrency=_integer(env, "MCP_MAX_CONCURRENCY", 8, 1, 64),
            mcp_max_response_bytes=_integer(env, "MCP_MAX_RESPONSE_BYTES", 1024 * 1024, 1024, 16 * 1024 * 1024),
            mcp_tool_profile=_choice(env, "MCP_TOOL_PROFILE", "observe", {"observe", "operate", "privileged"}),
            mcp_enable_file_read=_boolean(env, "MCP_ENABLE_FILE_READ", False),
            mcp_max_file_bytes=_integer(env, "MCP_MAX_FILE_BYTES", 256 * 1024, 1, 2 * 1024 * 1024),
            mcp_max_file_write_bytes=_integer(
                env,
                "MCP_MAX_FILE_WRITE_BYTES",
                256 * 1024,
                1,
                2 * 1024 * 1024,
            ),
            mcp_allowed_plugin_ids=_plugin_ids(env.get("MCP_ALLOWED_PLUGIN_IDS")),
            mcp_bind_host=str(env.get("MCP_BIND_HOST") or "127.0.0.1").strip(),
            mcp_bind_port=_integer(env, "MCP_BIND_PORT", 8000, 1, 65535),
            mcp_dns_rebinding_protection=_boolean(env, "MCP_DNS_REBINDING_PROTECTION", True),
            mcp_allowed_hosts=allowed_hosts,
        )

    @classmethod
    def from_toml(cls, config_path: str | Path) -> Settings:
        path = Path(config_path).expanduser()
        try:
            with path.open("rb") as config_file:
                values = tomllib.load(config_file)
        except OSError as exc:
            raise SettingsError(f"cannot read configuration file: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(f"configuration file is invalid TOML: {path}") from exc
        allowed_fields = set(_TOML_TO_ENV) | _TOML_INLINE_SECRET_FIELDS
        unknown = sorted(set(values) - allowed_fields)
        if unknown:
            raise SettingsError(f"unknown configuration field: {unknown[0]}")
        for inline_field, file_field in (
            ("leoai_password", "leoai_password_file"),
            ("mcp_client_token", "mcp_client_token_file"),
        ):
            if inline_field in values and file_field in values:
                raise SettingsError(f"configure only one of {inline_field} or {file_field}")
        if _TOML_INLINE_SECRET_FIELDS.intersection(values):
            try:
                mode = path.stat().st_mode
            except OSError as exc:
                raise SettingsError(f"cannot inspect configuration file: {path}") from exc
            if mode & 0o077:
                raise SettingsError("TOML with inline secrets must not be accessible by group or others")
        environment: dict[str, str] = {}
        inline_secrets: dict[str, str] = {}
        for key, value in values.items():
            if isinstance(value, dict):
                raise SettingsError(f"configuration field must be a scalar or list: {key}")
            if key in _TOML_INLINE_SECRET_FIELDS:
                if not isinstance(value, str) or not value:
                    raise SettingsError(f"configuration field must be a non-empty string: {key}")
                inline_secrets[key] = value
                continue
            if key in _TOML_LIST_FIELDS:
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise SettingsError(f"configuration field must be a string list: {key}")
                rendered = ",".join(value)
            elif isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (str, int, float)):
                rendered = str(value)
            else:
                raise SettingsError(f"configuration field has an unsupported value: {key}")
            if key in _TOML_PATH_FIELDS and rendered:
                secret_path = Path(rendered).expanduser()
                if not secret_path.is_absolute():
                    secret_path = path.parent / secret_path
                rendered = str(secret_path)
            environment[_TOML_TO_ENV[key]] = rendered
        return cls.from_env(environment, **inline_secrets)


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


def _read_optional_secret(env: Mapping[str, str], name: str) -> str | None:
    raw_path = str(env.get(name) or "").strip()
    return _read_secret(raw_path) if raw_path else None


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


def _choice(env: Mapping[str, str], name: str, default: str, choices: set[str]) -> str:
    value = str(env.get(name) or default).strip().lower()
    if value not in choices:
        expected = ", ".join(sorted(choices))
        raise SettingsError(f"{name} must be one of: {expected}")
    return value


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


def _plugin_ids(raw_value: object) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(item.strip() for item in str(raw_value or "").split(",") if item.strip()))
    if len(values) > 256:
        raise SettingsError("MCP_ALLOWED_PLUGIN_IDS must contain at most 256 IDs")
    if any(len(value) > 128 or re.fullmatch(r"[A-Za-z0-9._:-]+", value) is None for value in values):
        raise SettingsError("MCP_ALLOWED_PLUGIN_IDS contains an invalid plugin ID")
    return values
