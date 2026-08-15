from __future__ import annotations

import asyncio
import gzip
import json

import httpx
import pytest
from pydantic import SecretStr

from leoai_mcp_adapter.client import LeoAIClient
from leoai_mcp_adapter.config import Settings
from leoai_mcp_adapter.errors import LeoAIError


@pytest.mark.asyncio
async def test_client_logs_in_before_calling_leoai_and_reuses_the_session_cookie():
    observed: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        observed.append((request.method, request.url.path))
        if request.url.path == "/platform/user/login":
            assert json.loads(request.content) == {"username": "operator", "password": "correct horse"}
            return httpx.Response(
                200,
                json={"code": 200, "msg": "success", "data": {"userName": "operator"}},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/; HttpOnly"},
            )
        assert request.headers["cookie"] == "JSESSIONID=session-1"
        return httpx.Response(200, json={"code": 200, "msg": "success", "data": [{"projectId": "p-1"}]})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        result = await client.request("GET", "/platform/projects")

    assert result == [{"projectId": "p-1"}]
    assert observed == [
        ("POST", "/platform/user/login"),
        ("GET", "/platform/projects"),
    ]


@pytest.mark.asyncio
async def test_client_accepts_gzip_encoded_leoai_responses():
    payload = gzip.compress(json.dumps({"code": 200, "msg": "success"}).encode())

    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={
                "content-encoding": "gzip",
                "set-cookie": "JSESSIONID=session-1; Path=/; HttpOnly",
            },
        )

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        await client.check_ready()


@pytest.mark.asyncio
async def test_client_reports_missing_html_fallback_route_as_unsupported():
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200, "msg": "success"},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/; HttpOnly"},
            )
        return httpx.Response(200, text="<!doctype html><title>LeoAI</title>", headers={"content-type": "text/html"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.request("GET", "/platform/projects")

    assert error.value.code == "leoai_capability_unsupported"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_client_maps_method_not_supported_to_capability_error():
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200, "msg": "success"},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/; HttpOnly"},
            )
        return httpx.Response(500, json={"code": 500, "msg": "Request method 'POST' is not supported"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.request("POST", "/puppet-node/file/profile", json={"sessionId": "s-1"})

    assert error.value.code == "leoai_capability_unsupported"


@pytest.mark.asyncio
async def test_client_reauthenticates_once_when_leoai_session_expires():
    login_count = 0
    project_count = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, project_count
        if request.url.path == "/platform/user/login":
            login_count += 1
            return httpx.Response(
                200,
                json={"code": 200, "msg": "success"},
                headers={"set-cookie": f"JSESSIONID=session-{login_count}; Path=/; HttpOnly"},
            )
        project_count += 1
        if project_count == 1:
            return httpx.Response(401, json={"code": 401, "msg": "用户未登录"})
        assert request.headers["cookie"] == "JSESSIONID=session-2"
        return httpx.Response(200, json={"code": 200, "msg": "success", "data": []})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        assert await client.request("GET", "/platform/projects") == []

    assert login_count == 2
    assert project_count == 2


@pytest.mark.asyncio
async def test_client_does_not_replay_action_when_leoai_session_expires():
    login_count = 0
    action_count = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, action_count
        if request.url.path == "/platform/user/login":
            login_count += 1
            return httpx.Response(
                200,
                json={"code": 200, "msg": "success"},
                headers={"set-cookie": f"JSESSIONID=session-{login_count}; Path=/; HttpOnly"},
            )
        action_count += 1
        return httpx.Response(401, json={"code": 401, "msg": "用户未登录"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.request(
                "POST",
                "/puppet-node/file/new-dir",
                json={"sessionId": "s-1", "path": "/tmp/canary"},
                retry_on_auth_expiry=False,
            )

    assert error.value.code == "leoai_session_expired"
    assert login_count == 1
    assert action_count == 1


@pytest.mark.asyncio
async def test_client_maps_leoai_permission_denials_to_a_stable_error_code():
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200, "msg": "success"},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/"},
            )
        return httpx.Response(403, json={"code": 403, "msg": "无权限访问此会话"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as captured:
            await client.request("POST", "/puppet-node/capabilities", json={"sessionId": "s-1"})

    assert captured.value.code == "leoai_permission_denied"
    assert captured.value.retryable is False
    assert captured.value.message == "无权限访问此会话"


@pytest.mark.asyncio
async def test_client_reports_login_failure_without_echoing_the_password():
    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 401, "msg": "用户名或密码错误: correct horse"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as captured:
            await client.request("GET", "/platform/projects")

    assert captured.value.code == "leoai_auth_failed"
    assert captured.value.message == "LeoAI authentication failed"
    assert "correct horse" not in str(captured.value)


@pytest.mark.asyncio
async def test_client_maps_upstream_timeout_to_stable_retryable_error():
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/"},
            )
        raise httpx.ReadTimeout("upstream stalled", request=request)

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.request("GET", "/platform/projects")

    assert error.value.code == "leoai_timeout"
    assert error.value.retryable is True
    assert "upstream stalled" not in str(error.value)


@pytest.mark.asyncio
async def test_client_rejects_responses_larger_than_the_configured_limit():
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/"},
            )
        return httpx.Response(200, json={"code": 200, "data": {"value": "x" * 512}})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
        mcp_max_response_bytes=128,
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.request("GET", "/platform/projects")

    assert error.value.code == "leoai_protocol_error"
    assert error.value.message == "LeoAI response exceeded the configured size limit"


@pytest.mark.asyncio
async def test_cancelling_client_request_cancels_the_upstream_exchange():
    upstream_started = asyncio.Event()
    upstream_cancelled = asyncio.Event()

    async def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/"},
            )
        upstream_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            upstream_cancelled.set()
            raise
        raise AssertionError("unreachable")

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        request_task = asyncio.create_task(client.request("GET", "/platform/projects"))
        await upstream_started.wait()
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    assert upstream_cancelled.is_set()


@pytest.mark.asyncio
async def test_client_does_not_accept_business_success_inside_http_failure():
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/user/login":
            return httpx.Response(
                200,
                json={"code": 200},
                headers={"set-cookie": "JSESSIONID=session-1; Path=/"},
            )
        return httpx.Response(500, json={"code": 200, "data": {"looks": "successful"}})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.request("GET", "/platform/projects")

    assert error.value.code == "leoai_unavailable"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_login_business_success_without_jsessionid_is_not_ready():
    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "msg": "success"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.check_ready()

    assert error.value.code == "leoai_auth_failed"
    assert error.value.message == "LeoAI authentication did not establish a session"


@pytest.mark.asyncio
async def test_login_requiring_password_change_is_not_ready():
    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 200,
                "msg": "success",
                "data": {"passwordChangeRequired": True},
            },
            headers={"set-cookie": "JSESSIONID=session-1; Path=/; HttpOnly"},
        )

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.check_ready()

    assert error.value.code == "leoai_auth_failed"
    assert error.value.message == "LeoAI account requires a password change"


@pytest.mark.asyncio
async def test_client_migrates_required_initial_password_before_ready():
    observed: list[tuple[str, str]] = []
    migrated = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal migrated
        observed.append((request.method, request.url.path))
        if request.url.path == "/platform/user/login":
            body = json.loads(request.content)
            if body["password"] == "target-password" and not migrated:
                return httpx.Response(401, json={"code": 401, "msg": "bad credentials"})
            if body["password"] == "target-password":
                return httpx.Response(
                    200,
                    json={"code": 200, "data": {"userName": "operator"}},
                    headers={"set-cookie": "JSESSIONID=target-session; Path=/; HttpOnly"},
                )
            assert body == {"username": "operator", "password": "54ikun"}
            return httpx.Response(
                200,
                json={"code": 200, "data": {"passwordChangeRequired": True}},
                headers={"set-cookie": "JSESSIONID=initial-session; Path=/; HttpOnly"},
            )
        if request.url.path == "/platform/user/change-password":
            migrated = True
            assert request.headers["cookie"] == "JSESSIONID=initial-session"
            assert json.loads(request.content) == {
                "oldPassword": "54ikun",
                "newPassword": "target-password",
            }
            return httpx.Response(200, json={"code": 200, "data": None})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("target-password"),
        mcp_client_token=SecretStr("adapter-token"),
        leoai_initial_password=SecretStr("54ikun"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        await client.check_ready()

    assert observed == [
        ("POST", "/platform/user/login"),
        ("POST", "/platform/user/login"),
        ("POST", "/platform/user/change-password"),
        ("POST", "/platform/user/login"),
    ]


@pytest.mark.asyncio
async def test_client_does_not_change_a_non_initial_password():
    observed: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        if request.url.path == "/platform/user/login":
            if json.loads(request.content)["password"] == "target-password":
                return httpx.Response(401, json={"code": 401, "msg": "bad credentials"})
            return httpx.Response(
                200,
                json={"code": 200, "data": {"passwordChangeRequired": False}},
                headers={"set-cookie": "JSESSIONID=existing-session; Path=/; HttpOnly"},
            )
        raise AssertionError("password change must not be attempted")

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("target-password"),
        mcp_client_token=SecretStr("adapter-token"),
        leoai_initial_password=SecretStr("54ikun"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        with pytest.raises(LeoAIError) as error:
            await client.check_ready()

    assert error.value.code == "leoai_auth_failed"
    assert observed == ["/platform/user/login", "/platform/user/login"]


@pytest.mark.asyncio
async def test_client_does_not_inherit_host_proxy_environment(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("correct horse"),
        mcp_client_token=SecretStr("adapter-token"),
    )

    client = LeoAIClient(settings)
    await client.aclose()


@pytest.mark.asyncio
async def test_authentication_failure_is_latched_to_avoid_login_probe_storms():
    login_requests = 0

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal login_requests
        login_requests += 1
        return httpx.Response(401, json={"code": 401, "msg": "bad credentials"})

    settings = Settings(
        leoai_base_url="https://leoai.internal",
        leoai_username="operator",
        leoai_password=SecretStr("wrong password"),
        mcp_client_token=SecretStr("adapter-token"),
    )
    client = LeoAIClient(settings, transport=httpx.MockTransport(upstream))

    async with client:
        for _attempt in range(2):
            with pytest.raises(LeoAIError) as error:
                await client.check_ready()
            assert error.value.code == "leoai_auth_failed"

    assert login_requests == 1
