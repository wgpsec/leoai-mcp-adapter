from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

from .client import LeoAIClient
from .errors import LeoAIError


class LeoAITools:
    def __init__(self, client: LeoAIClient, *, max_concurrency: int, max_file_bytes: int) -> None:
        self._client = client
        self._max_concurrency = max_concurrency
        self._max_file_bytes = max_file_bytes
        self._active_requests = 0
        self._limit_lock = asyncio.Lock()

    async def list_projects(self) -> dict[str, object]:
        data = await self._request("GET", "/platform/projects")
        if not isinstance(data, list):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid project list")
        return {
            "untrusted_external_content": True,
            "projects": [_project_summary(item) for item in data],
        }

    async def list_project_puppets(self, project_id: str) -> dict[str, object]:
        data = await self._request("GET", f"/platform/projects/{project_id}/puppets")
        if not isinstance(data, list):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid Puppet list")
        return {
            "untrusted_external_content": True,
            "puppets": [_puppet_summary(item) for item in data],
        }

    async def list_sessions(self, project_id: str | None = None) -> dict[str, object]:
        params = {"projectId": project_id} if project_id is not None else None
        data = await self._request("GET", "/platform/session/sessions", params=params)
        if not isinstance(data, list):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid session list")
        return {
            "untrusted_external_content": True,
            "sessions": [_session_summary(item) for item in data],
        }

    async def get_session_capabilities(self, session_id: str) -> dict[str, object]:
        data = await self._request(
            "POST",
            "/puppet-node/capabilities",
            json={"sessionId": session_id},
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned invalid session capabilities")
        session = _pick(
            data,
            "sessionId",
            "puppetId",
            "cacheMode",
            "capabilities",
            "capabilityCount",
        )
        if "runtimeProfile" in data:
            profile = data["runtimeProfile"]
            if not isinstance(profile, dict):
                raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid runtime profile")
            session["runtimeProfile"] = _pick(
                profile,
                "runtime",
                "version",
                "sapi",
                "osFamily",
                "architecture",
                "extensions",
                "disabledFunctions",
                "capabilities",
            )
        return {"untrusted_external_content": True, "session": session}

    async def get_current_host(self, session_id: str) -> dict[str, object]:
        data = await self._request(
            "POST",
            "/puppet-node/current-host",
            json={"sessionId": session_id},
        )
        return {
            "untrusted_external_content": True,
            "puppet": _puppet_summary(data),
        }

    async def get_basic_info(self, session_id: str) -> dict[str, object]:
        data = await self._request(
            "POST",
            "/puppet-node/basic-info",
            json={"sessionId": session_id},
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned invalid basic host information")
        return {
            "untrusted_external_content": True,
            "basicInfo": _sanitize_external(data),
        }

    async def get_recon_summary(self, session_id: str) -> dict[str, object]:
        data = await self._request(
            "POST",
            "/platform/session/recon-summary",
            json={"sessionId": session_id},
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid reconnaissance summary")
        return {
            "untrusted_external_content": True,
            "reconSummary": _pick(data, "sessionId", "reconSummary", "hasReconSummary"),
        }

    async def get_file_profile(self, session_id: str) -> dict[str, object]:
        data = await self._request(
            "POST",
            "/puppet-node/file/profile",
            json={"sessionId": session_id},
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid filesystem profile")
        return {
            "untrusted_external_content": True,
            "fileProfile": _pick(
                data,
                "osFamily",
                "pathStyle",
                "separator",
                "caseSensitivity",
                "roots",
                "capabilities",
            ),
        }

    async def list_files(self, session_id: str, path: str) -> dict[str, object]:
        data = await self._request(
            "POST",
            "/puppet-node/file/list",
            json={"sessionId": session_id, "path": path},
        )
        if not isinstance(data, dict) or not isinstance(data.get("fileList"), list):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid directory listing")
        directory = _pick(data, "absolutePath", "count")
        directory["files"] = [_file_summary(item) for item in data["fileList"]]
        return {"untrusted_external_content": True, "directory": directory}

    async def read_file(
        self,
        session_id: str,
        path: str,
        offset: int,
        max_bytes: int,
    ) -> dict[str, object]:
        effective_bytes = min(max_bytes, self._max_file_bytes)
        data = await self._request(
            "POST",
            "/puppet-node/file/preview-chunk",
            json={
                "sessionId": session_id,
                "path": path,
                "offset": offset,
                "size": effective_bytes,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), str):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid file chunk")
        try:
            decoded = base64.b64decode(data["data"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise LeoAIError("leoai_protocol_error", "LeoAI returned invalid base64 file data") from exc
        if len(decoded) > effective_bytes:
            raise LeoAIError("leoai_protocol_error", "LeoAI returned a file chunk larger than requested")
        total_size = data.get("size")
        if not isinstance(total_size, int) or total_size < 0:
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid file size")
        return {
            "untrusted_external_content": True,
            "fileChunk": {
                "data": data["data"],
                "totalSize": total_size,
                "offset": offset,
                "nextOffset": offset + len(decoded),
                "truncated": bool(data.get("truncated")),
            },
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with self._limit_lock:
            if self._active_requests >= self._max_concurrency:
                raise LeoAIError(
                    "adapter_rate_limited",
                    "adapter concurrency limit exceeded",
                    True,
                )
            self._active_requests += 1
        try:
            return await self._client.request(method, path, **kwargs)
        finally:
            async with self._limit_lock:
                self._active_requests -= 1


def _project_summary(value: Any) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("project"), dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid project")
    project = value["project"]
    return _pick(
        project,
        "projectId",
        "projectName",
        "projectCode",
        "description",
        "status",
        "permission",
    ) | _pick(value, "hostCount", "activeSessionCount", "manageable", "contentEditable")


def _puppet_summary(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid Puppet")
    return _pick(
        value,
        "puppetId",
        "puppetName",
        "parentPuppetId",
        "protocol",
        "type",
        "permission",
        "lastHeartbeat",
        "remark",
    )


def _session_summary(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid session")
    return _pick(
        value,
        "sessionId",
        "projectId",
        "puppetId",
        "puppetName",
        "parentPuppetId",
        "updateTime",
        "lastActiveTime",
        "cacheMode",
        "capabilities",
    )


def _file_summary(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned invalid file metadata")
    return _pick(
        value,
        "name",
        "path",
        "size",
        "modified",
        "isDirectory",
        "isFile",
        "canRead",
        "canWrite",
        "canExecute",
        "exists",
        "extension",
    )


def _pick(source: dict[str, Any], *fields: str) -> dict[str, object]:
    return {field: source[field] for field in fields if field in source}


_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "connlink",
    "cookie",
    "createbyuserid",
    "headers",
    "owneruserid",
    "password",
    "secret",
    "teamid",
}


def _sanitize_external(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_external(item) for key, item in value.items() if not _is_sensitive_key(str(key))}
    if isinstance(value, list):
        return [_sanitize_external(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return (
        normalized in _SENSITIVE_KEYS
        or "password" in normalized
        or "secret" in normalized
        or "token" in normalized
        or normalized.startswith("proxy")
        or normalized.endswith("disguiseid")
        or normalized.endswith("strategy")
    )
