from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

import httpx

from .config import Settings
from .errors import LeoAIError


class LeoAIClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.leoai_base_url,
            follow_redirects=False,
            timeout=httpx.Timeout(
                settings.leoai_read_timeout_seconds,
                connect=settings.leoai_connect_timeout_seconds,
            ),
            verify=settings.leoai_tls_verify,
            trust_env=False,
            transport=transport,
        )
        self._authenticated = False
        self._auth_failure: LeoAIError | None = None
        self._login_lock = asyncio.Lock()
        self._max_response_bytes = settings.mcp_max_response_bytes

    async def __aenter__(self) -> LeoAIClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def check_ready(self) -> None:
        await self._ensure_authenticated()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        retry_on_auth_expiry: bool = True,
    ) -> Any:
        await self._ensure_authenticated()
        response = await self._send(method, path, params=params, json=json)
        if _is_unauthorized(response):
            self._authenticated = False
            if retry_on_auth_expiry:
                await self._ensure_authenticated()
                response = await self._send(method, path, params=params, json=json)
        return _response_data(response)

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with self._http.stream(method, path, **kwargs) as response:
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > self._max_response_bytes:
                    raise _response_too_large()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise _response_too_large()
                headers = response.headers.copy()
                for name in ("content-encoding", "content-length", "transfer-encoding"):
                    if name in headers:
                        del headers[name]
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=bytes(content),
                    request=response.request,
                )
        except httpx.TimeoutException as exc:
            raise LeoAIError("leoai_timeout", "LeoAI request timed out", True) from exc
        except httpx.RequestError as exc:
            raise LeoAIError("leoai_unavailable", "LeoAI is unavailable", True) from exc

    async def _ensure_authenticated(self) -> None:
        if self._auth_failure is not None:
            raise self._auth_failure
        if self._authenticated:
            return
        async with self._login_lock:
            if self._auth_failure is not None:
                raise self._auth_failure
            if self._authenticated:
                return
            try:
                authentication = await self._login(self._settings.leoai_password.get_secret_value())
                _reject_required_password_change(authentication)
            except LeoAIError as error:
                initial_password = self._settings.leoai_initial_password
                if (
                    error.code != "leoai_auth_failed"
                    or initial_password is None
                    or initial_password.get_secret_value() == self._settings.leoai_password.get_secret_value()
                ):
                    self._latch_auth_failure(error)
                    raise
                try:
                    initial_authentication = await self._login(initial_password.get_secret_value())
                    if not _password_change_required(initial_authentication):
                        self._latch_auth_failure(error)
                        raise error
                    await self._change_password(initial_password.get_secret_value())
                    authentication = await self._login(self._settings.leoai_password.get_secret_value())
                    _reject_required_password_change(authentication)
                except LeoAIError as migration_error:
                    if migration_error is error:
                        raise
                    wrapped = LeoAIError(
                        "leoai_auth_failed",
                        "LeoAI initial password migration failed",
                    )
                    self._latch_auth_failure(wrapped)
                    raise wrapped from migration_error
            self._authenticated = True

    async def _login(self, password: str) -> Any:
        response = await self._send(
            "POST",
            "/platform/user/login",
            json={
                "username": self._settings.leoai_username,
                "password": password,
            },
        )
        authentication = _response_data(response, authenticating=True)
        if not any(cookie.name == "JSESSIONID" for cookie in response.cookies.jar):
            raise LeoAIError(
                "leoai_auth_failed",
                "LeoAI authentication did not establish a session",
            )
        return authentication

    async def _change_password(self, old_password: str) -> None:
        try:
            response = await self._send(
                "POST",
                "/platform/user/change-password",
                json={
                    "oldPassword": old_password,
                    "newPassword": self._settings.leoai_password.get_secret_value(),
                },
            )
            _response_data(response)
        except LeoAIError as error:
            raise LeoAIError("leoai_auth_failed", "LeoAI initial password migration failed") from error

    def _latch_auth_failure(self, error: LeoAIError) -> None:
        if error.code == "leoai_auth_failed":
            self._auth_failure = error


def _response_data(response: httpx.Response, *, authenticating: bool = False) -> Any:
    if "text/html" in response.headers.get("content-type", "").lower():
        if authenticating:
            raise LeoAIError("leoai_auth_failed", "LeoAI authentication failed")
        raise LeoAIError(
            "leoai_capability_unsupported",
            "LeoAI endpoint is not available in this release",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise LeoAIError("leoai_protocol_error", "LeoAI returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid response envelope")
    code = _response_code(response, payload)
    if code != 200:
        if authenticating:
            raise LeoAIError("leoai_auth_failed", "LeoAI authentication failed")
        raise LeoAIError(*_mapped_error(code, payload.get("msg")))
    return payload.get("data")


def _password_change_required(authentication: Any) -> bool:
    return isinstance(authentication, dict) and authentication.get("passwordChangeRequired") is True


def _reject_required_password_change(authentication: Any) -> None:
    if _password_change_required(authentication):
        raise LeoAIError(
            "leoai_auth_failed",
            "LeoAI account requires a password change",
        )


def _is_unauthorized(response: httpx.Response) -> bool:
    if response.status_code == 401:
        return True
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("code") == 401


def _response_code(response: httpx.Response, payload: dict[str, Any]) -> int:
    if not 200 <= response.status_code < 300:
        return response.status_code
    raw_code = payload.get("code", response.status_code)
    try:
        return int(raw_code)
    except (TypeError, ValueError) as exc:
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid response code") from exc


def _mapped_error(code: int, raw_message: object) -> tuple[str, str, bool]:
    message = str(raw_message or "LeoAI request failed")
    normalized_message = message.lower()
    if code == 405 or "not supported" in normalized_message or "不支持" in message:
        return ("leoai_capability_unsupported", "LeoAI endpoint is not available in this release", False)
    if code == 401:
        return ("leoai_session_expired", "LeoAI session expired", True)
    if code == 403:
        return ("leoai_permission_denied", message, False)
    if code == 404:
        return ("leoai_not_found", message, False)
    if code == 429:
        return ("leoai_unavailable", "LeoAI rate limit exceeded", True)
    if code >= 500:
        return ("leoai_unavailable", "LeoAI is unavailable", True)
    return ("leoai_protocol_error", message, False)


def _response_too_large() -> LeoAIError:
    return LeoAIError(
        "leoai_protocol_error",
        "LeoAI response exceeded the configured size limit",
    )
