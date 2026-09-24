from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
from typing import Any

from .client import LeoAIClient
from .errors import LeoAIError

_NETWORK_PROBE_PREFIX = "/puppet-node/network-probe/workflow"
_NETWORK_PROBE_STAGES = ("REACHABILITY", "PORT_SCAN", "SERVICE_PROBE", "FINGERPRINT")


def build_network_probe_scan(
    *,
    targets: list[str],
    exclude: list[str] | None = None,
    name: str | None = None,
    port_profile: str | None = None,
    port_ranges: list[str] | None = None,
    ports: list[int] | None = None,
    exclude_ports: list[int] | None = None,
    workers: int | None = None,
    timeout_ms: int | None = None,
    stages: list[str] | None = None,
    fingerprint_tags: list[str] | None = None,
    fingerprint_ids: list[str] | None = None,
) -> dict[str, object]:
    fingerprint_tags = [tag for tag in fingerprint_tags or [] if tag]
    fingerprint_ids = [item for item in fingerprint_ids or [] if item]
    target_input: dict[str, object] = {"items": list(targets)}
    if exclude:
        target_input["exclude"] = list(exclude)
    scan: dict[str, object] = {"targets": target_input}
    if name:
        scan["name"] = name
    port_policy = _network_probe_port_policy(port_profile, port_ranges, ports, exclude_ports)
    if port_policy is not None:
        scan["portPolicy"] = port_policy
    execution: dict[str, object] = {}
    if workers is not None:
        execution["workers"] = workers
    if timeout_ms is not None:
        execution["timeoutMs"] = timeout_ms
    if execution:
        scan["execution"] = execution
    if fingerprint_tags or fingerprint_ids:
        fingerprint: dict[str, object] = {}
        if fingerprint_tags:
            fingerprint["tags"] = fingerprint_tags
        if fingerprint_ids:
            fingerprint["ids"] = fingerprint_ids
        scan["fingerprint"] = fingerprint
    resolved_stages = _resolve_network_probe_stages(
        stages,
        has_fingerprint=bool(fingerprint_tags or fingerprint_ids),
    )
    if resolved_stages is not None:
        scan["stages"] = resolved_stages
    return scan


def _network_probe_port_policy(
    profile: str | None,
    ranges: list[str] | None,
    ports: list[int] | None,
    exclude_ports: list[int] | None,
) -> dict[str, object] | None:
    if profile is None and not ranges and not ports and not exclude_ports:
        return None
    resolved_profile = profile
    if resolved_profile is None:
        resolved_profile = "custom" if ports or ranges else "standard"
    if resolved_profile == "custom" and not ports and not ranges:
        raise LeoAIError("tool_input_invalid", "custom port profile requires ports or portRanges")
    policy: dict[str, object] = {"profile": resolved_profile}
    if ranges:
        policy["ranges"] = list(ranges)
    if ports:
        policy["include"] = list(ports)
    if exclude_ports:
        policy["exclude"] = list(exclude_ports)
    return policy


def _resolve_network_probe_stages(
    stages: list[str] | None,
    *,
    has_fingerprint: bool,
) -> list[str] | None:
    if stages is None:
        return list(_NETWORK_PROBE_STAGES) if has_fingerprint else None
    selected: list[str] = []
    seen: set[str] = set()
    for stage in stages:
        name = stage.strip().upper()
        if name not in _NETWORK_PROBE_STAGES:
            raise LeoAIError("tool_input_invalid", f"unsupported network probe stage: {stage}")
        if name in seen:
            continue
        selected.append(name)
        seen.add(name)
    if not selected:
        raise LeoAIError("tool_input_invalid", "at least one network probe stage is required")
    if "SERVICE_PROBE" in seen and "PORT_SCAN" not in seen:
        raise LeoAIError("tool_input_invalid", "SERVICE_PROBE requires PORT_SCAN")
    if "FINGERPRINT" in seen and "SERVICE_PROBE" not in seen:
        raise LeoAIError("tool_input_invalid", "FINGERPRINT requires SERVICE_PROBE and PORT_SCAN")
    if has_fingerprint and "FINGERPRINT" not in seen:
        raise LeoAIError("tool_input_invalid", "fingerprint tags or ids require the FINGERPRINT stage")
    return [stage for stage in _NETWORK_PROBE_STAGES if stage in seen]


class LeoAITools:
    def __init__(
        self,
        client: LeoAIClient,
        *,
        protocol_profile: str,
        max_concurrency: int,
        max_file_bytes: int,
        max_file_write_bytes: int,
        allowed_plugin_ids: tuple[str, ...],
    ) -> None:
        self._client = client
        self._protocol_profile = protocol_profile
        self._max_concurrency = max_concurrency
        self._max_file_bytes = max_file_bytes
        self._max_file_write_bytes = max_file_write_bytes
        self._allowed_plugin_ids = frozenset(allowed_plugin_ids)
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
        try:
            data = await self._request("GET", f"/platform/projects/{project_id}/puppets")
        except LeoAIError as error:
            if error.code != "leoai_permission_denied":
                raise
            try:
                await self._request("GET", "/platform/projects")
            except LeoAIError as probe_error:
                if probe_error.code == "leoai_capability_unsupported":
                    raise probe_error from error
            raise
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
        try:
            data = await self._request(
                "POST",
                "/puppet-node/file/profile",
                json={"sessionId": session_id},
            )
        except LeoAIError as error:
            if self._protocol_profile != "1x" or error.code != "leoai_capability_unsupported":
                raise
            root_listing = await self._request(
                "POST",
                "/puppet-node/file/list-root",
                json={"sessionId": session_id},
            )
            data = _profile_from_root_listing(root_listing)
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

    async def list_disguises(self) -> dict[str, object]:
        data = await self._request("GET", "/platform/disguise-manager/disguises")
        if not isinstance(data, list):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid disguise list")
        return {
            "untrusted_external_content": True,
            "disguises": [_disguise_summary(item) for item in data],
        }

    async def list_shell_generator_types(self) -> dict[str, object]:
        data = await self._request("GET", "/platform/shell-generator/supported-types")
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned invalid generator types")
        catalog: dict[str, object] = {}
        for field in (
            "transportProtocols",
            "runtimeGenerators",
            "targetJavaVersions",
            "servletNamespaces",
            "serverInjectorTypes",
            "serverProtocolInjectorTypes",
        ):
            if field in data:
                catalog[field] = _sanitize_external(data[field])
        injectors = data.get("serverInjectorTypes")
        if isinstance(injectors, dict):
            catalog["serverTypes"] = sorted(str(name) for name in injectors)
        packer_names = _packer_names(data.get("packerTypes"))
        if packer_names:
            catalog["packerTypes"] = packer_names
        return {"untrusted_external_content": True, "generator": catalog}

    async def create_project(
        self,
        project_name: str,
        *,
        project_code: str | None = None,
        description: str | None = None,
        permission: str = "private",
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "projectName": project_name,
            "permission": permission,
        }
        if project_code is not None:
            payload["projectCode"] = project_code
        if description is not None:
            payload["description"] = description
        data = await self._action_request("POST", "/platform/projects", json=payload)
        if not isinstance(data, dict) or not isinstance(data.get("projectId"), str):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid created project")
        return {
            "untrusted_external_content": True,
            "project": _pick(
                data,
                "projectId",
                "projectName",
                "projectCode",
                "description",
                "status",
                "permission",
            ),
        }

    async def generate_runtime_artifact(
        self,
        runtime: str,
        artifact_type: str,
        req_disguise_id: str,
        resp_disguise_id: str,
        *,
        payload_key: str | None = None,
    ) -> dict[str, object]:
        payload_key = _resolve_payload_key(payload_key)
        data = await self._action_request(
            "POST",
            "/platform/shell-generator/generate/runtime",
            json={
                "runtime": runtime,
                "artifactType": artifact_type,
                "reqDisguiseId": req_disguise_id,
                "respDisguiseId": resp_disguise_id,
                "payloadKey": payload_key,
            },
        )
        return _generated_artifact("runtime", data, payload_key=payload_key)

    async def generate_webshell(
        self,
        shell_type: str,
        req_disguise_id: str,
        resp_disguise_id: str,
        *,
        protocol: str | None = None,
        payload_key: str | None = None,
    ) -> dict[str, object]:
        payload_key = _resolve_payload_key(payload_key)
        payload: dict[str, object] = {
            "shellType": shell_type,
            "reqDisguiseId": req_disguise_id,
            "respDisguiseId": resp_disguise_id,
            "payloadKey": payload_key,
        }
        if protocol is not None:
            payload["protocol"] = protocol
        data = await self._action_request(
            "POST",
            "/platform/shell-generator/generate/webshell",
            json=payload,
        )
        return _generated_artifact("webshell", data, payload_key=payload_key)

    async def generate_memory_shell(
        self,
        server_type: str,
        shell_type: str,
        packer_type: str,
        req_disguise_id: str,
        resp_disguise_id: str,
        *,
        protocol: str | None = None,
        server_version: str | None = None,
        url_pattern: str | None = None,
        header_name: str | None = None,
        header_value: str | None = None,
        target_java_version: str | None = None,
        servlet_namespace: str | None = None,
        bypass_java_module: bool | None = None,
        payload_key: str | None = None,
    ) -> dict[str, object]:
        protocol_value = protocol or "http"
        if protocol_value in {"http", "httpchunk"} and (not header_name or not header_value):
            raise LeoAIError(
                "tool_input_invalid",
                "http/httpchunk memory shells require headerName and headerValue",
            )
        payload_key = _resolve_payload_key(payload_key)
        payload: dict[str, object] = {
            "serverType": server_type,
            "shellType": shell_type,
            "packerType": packer_type,
            "reqDisguiseId": req_disguise_id,
            "respDisguiseId": resp_disguise_id,
            "payloadKey": payload_key,
        }
        if protocol is not None:
            payload["protocol"] = protocol
        if server_version is not None:
            payload["serverVersion"] = server_version
        if url_pattern is not None:
            payload["urlPattern"] = url_pattern
        if header_name is not None:
            payload["headerName"] = header_name
        if header_value is not None:
            payload["headerValue"] = header_value
        if target_java_version is not None:
            payload["targetJavaVersion"] = target_java_version
        if servlet_namespace is not None:
            payload["servletNamespace"] = servlet_namespace
        if bypass_java_module is not None:
            payload["byPassJavaModule"] = bypass_java_module
        data = await self._action_request(
            "POST",
            "/platform/shell-generator/generate/memoryshell",
            json=payload,
        )
        return _generated_artifact("memoryshell", data, payload_key=payload_key)

    async def add_puppet(
        self,
        puppet_name: str,
        conn_link: str,
        req_disguise_id: str,
        resp_disguise_id: str,
        *,
        protocol: str,
        puppet_type: str,
        payload_key: str,
        project_id: str | None = None,
        permission: str = "private",
        remark: str | None = None,
        parent_puppet_id: str = "root",
        header_name: str | None = None,
        header_value: str | None = None,
    ) -> dict[str, object]:
        if (header_name is None) != (header_value is None):
            raise LeoAIError(
                "tool_input_invalid",
                "headerName and headerValue must be provided together",
            )
        payload: dict[str, object] = {
            "puppetName": puppet_name,
            "connLink": conn_link,
            "protocol": protocol,
            "type": puppet_type,
            "reqDisguiseId": req_disguise_id,
            "respDisguiseId": resp_disguise_id,
            "payloadKey": payload_key,
            "permission": permission,
            "parentPuppetId": parent_puppet_id,
        }
        if remark is not None:
            payload["remark"] = remark
        if header_name is not None and header_value is not None:
            payload["headers"] = json.dumps({header_name: header_value}, separators=(",", ":"))
        params = {"projectId": project_id} if project_id is not None else None
        data = await self._action_request(
            "POST",
            "/platform/puppet-manage/puppets",
            params=params,
            json=payload,
        )
        if not isinstance(data, dict) or not isinstance(data.get("puppetId"), str):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid created Puppet")
        result: dict[str, object] = {
            "operation": "puppet_added",
            "puppetId": data["puppetId"],
            "puppetName": puppet_name,
            "protocol": protocol,
            "type": puppet_type,
        }
        if project_id is not None:
            result["projectId"] = project_id
        return result

    async def open_session(self, puppet_id: str, project_id: str | None = None) -> dict[str, object]:
        params = {"puppetId": puppet_id}
        if project_id is not None:
            params["projectId"] = project_id
        data = await self._action_request("GET", "/puppet-node/init", params=params)
        if not isinstance(data, dict) or not isinstance(data.get("sessionId"), str):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid opened session")
        session = _pick(data, "sessionId", "puppetId", "projectId", "cacheMode", "capabilities")
        session.setdefault("puppetId", puppet_id)
        return {"untrusted_external_content": True, "session": session}

    async def close_session(self, session_id: str) -> dict[str, object]:
        await self._action_request(
            "POST",
            "/platform/session/sessions/delete",
            json={"sessionId": session_id},
        )
        return {"operation": "session_closed", "sessionId": session_id}

    async def terminal_action(
        self,
        session_id: str,
        terminal_id: str,
        *,
        operation: str,
        command_type: str,
        command: str,
        include_output: bool = False,
    ) -> dict[str, object]:
        request = self._request if command_type == "read" else self._action_request
        payload: dict[str, object] = {
            "sessionId": session_id,
            "processId": terminal_id,
            "cmd": command,
            "type": command_type,
        }
        if include_output:
            payload["includeOutput"] = True
        data = await request(
            "POST",
            "/puppet-node/command/exec-command",
            json=payload,
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid terminal result")
        return {
            "untrusted_external_content": True,
            "terminal": {
                "terminalId": terminal_id,
                "operation": operation,
                "result": _sanitize_external(data),
            },
        }

    async def create_file(self, session_id: str, path: str, content: str) -> dict[str, object]:
        self._validate_file_content(content)
        return await self._file_action(
            "created",
            path,
            "/puppet-node/file/new-file",
            {"sessionId": session_id, "path": path, "content": content},
        )

    async def edit_file(self, session_id: str, path: str, content: str) -> dict[str, object]:
        self._validate_file_content(content)
        return await self._file_action(
            "edited",
            path,
            "/puppet-node/file/edit",
            {"sessionId": session_id, "path": path, "content": content},
        )

    async def create_directory(self, session_id: str, path: str) -> dict[str, object]:
        return await self._file_action(
            "directory_created",
            path,
            "/puppet-node/file/new-dir",
            {"sessionId": session_id, "path": path},
        )

    async def move_file(self, session_id: str, path: str, new_path: str) -> dict[str, object]:
        return await self._file_action(
            "moved",
            path,
            "/puppet-node/file/move",
            {
                "sessionId": session_id,
                "path": path,
                "newPath": new_path,
                "conflictStrategy": "skip",
            },
            destination_path=new_path,
        )

    async def copy_file(self, session_id: str, path: str, destination_path: str) -> dict[str, object]:
        return await self._file_action(
            "copied",
            path,
            "/puppet-node/file/copy",
            {
                "sessionId": session_id,
                "path": path,
                "destPath": destination_path,
                "conflictStrategy": "skip",
            },
            destination_path=destination_path,
        )

    async def delete_file(self, session_id: str, path: str) -> dict[str, object]:
        return await self._file_action(
            "deleted",
            path,
            "/puppet-node/file/delete",
            {"sessionId": session_id, "path": path},
        )

    async def list_processes(self, session_id: str) -> dict[str, object]:
        return await self._system_query(
            "processes",
            "/puppet-node/process/list",
            {"sessionId": session_id},
        )

    async def find_processes(
        self,
        session_id: str,
        *,
        name: str | None,
        pid: int | None,
        port: int | None,
    ) -> dict[str, object]:
        if name is None and pid is None and port is None:
            raise LeoAIError(
                "tool_input_invalid",
                "at least one process filter is required",
            )
        payload: dict[str, object] = {"sessionId": session_id}
        if name is not None:
            payload["name"] = name
        if pid is not None:
            payload["pid"] = pid
        if port is not None:
            payload["port"] = port
        return await self._system_query("processes", "/puppet-node/process/find", payload)

    async def kill_process(self, session_id: str, pid: int, force: bool) -> dict[str, object]:
        return await self._system_action(
            "process_killed",
            str(pid),
            "/puppet-node/process/kill",
            {"sessionId": session_id, "pid": pid, "force": force},
        )

    async def list_services(self, session_id: str) -> dict[str, object]:
        return await self._system_query(
            "services",
            "/puppet-node/service/list",
            {"sessionId": session_id},
        )

    async def query_service(self, session_id: str, service_name: str) -> dict[str, object]:
        return await self._system_query(
            "service",
            "/puppet-node/service/query",
            {"sessionId": session_id, "serviceName": service_name},
        )

    async def control_service(
        self,
        session_id: str,
        service_name: str,
        action: str,
    ) -> dict[str, object]:
        endpoints = {
            "start": "/puppet-node/service/start",
            "stop": "/puppet-node/service/stop",
            "restart": "/puppet-node/service/restart",
        }
        endpoint = endpoints.get(action)
        if endpoint is None:
            raise LeoAIError("tool_input_invalid", "unsupported service action")
        return await self._system_action(
            f"service_{action}",
            service_name,
            endpoint,
            {"sessionId": session_id, "serviceName": service_name},
        )

    async def list_network_connections(
        self,
        session_id: str,
        *,
        state: str | None,
        protocol: str | None,
        port: int | None,
        pid: int | None,
        process: str | None,
        remote_ip: str | None,
        listening_only: bool,
        max_entries: int,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "sessionId": session_id,
            "listeningOnly": listening_only,
            "maxEntries": max_entries,
        }
        for key, value in (
            ("state", state),
            ("protocol", protocol),
            ("port", str(port) if port is not None else None),
            ("pid", str(pid) if pid is not None else None),
            ("process", process),
            ("remoteIp", remote_ip),
        ):
            if value is not None:
                payload[key] = value
        return await self._system_query(
            "networkConnections",
            "/puppet-node/network-connection/list",
            payload,
        )

    async def get_network_connection_summary(self, session_id: str) -> dict[str, object]:
        return await self._system_query(
            "networkConnectionSummary",
            "/puppet-node/network-connection/summary",
            {"sessionId": session_id},
        )

    async def preview_network_probe(
        self,
        session_id: str,
        scan: dict[str, object],
    ) -> dict[str, object]:
        data = await self._request(
            "POST",
            f"{_NETWORK_PROBE_PREFIX}/preview",
            json={"sessionId": session_id, "scan": scan},
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid network probe preview")
        return {
            "untrusted_external_content": True,
            "scanPreview": _sanitize_external(data),
        }

    async def start_network_probe(
        self,
        session_id: str,
        scan: dict[str, object],
    ) -> dict[str, object]:
        data = await self._action_request(
            "POST",
            f"{_NETWORK_PROBE_PREFIX}/start",
            json={"sessionId": session_id, "scan": scan},
        )
        return self._scan_start_result("network-probe", data)

    async def query_network_probe(
        self,
        session_id: str,
        task_id: str,
        *,
        view: str,
        page: int | None,
        page_size: int | None,
        endpoint_id: str | None = None,
    ) -> dict[str, object]:
        if view == "summary":
            if page is not None or page_size is not None or endpoint_id is not None:
                raise LeoAIError(
                    "tool_input_invalid",
                    "page, pageSize, and endpointId are only valid for results or fingerprints",
                )
            data = await self._request(
                "POST",
                f"{_NETWORK_PROBE_PREFIX}/query",
                json={"sessionId": session_id, "taskId": task_id},
            )
            return self._scan_result("network-probe", "queried", data, task_id=task_id)
        if view == "results":
            if endpoint_id is not None:
                raise LeoAIError("tool_input_invalid", "endpointId is only valid for fingerprints")
            data = await self._request(
                "POST",
                f"{_NETWORK_PROBE_PREFIX}/results/query",
                json={
                    "sessionId": session_id,
                    "taskId": task_id,
                    "page": 1 if page is None else page,
                    "pageSize": 50 if page_size is None else page_size,
                },
            )
            return self._scan_result("network-probe", "results", data, task_id=task_id)
        if view != "fingerprints":
            raise LeoAIError("tool_input_invalid", "unsupported network probe view")
        payload: dict[str, object] = {
            "sessionId": session_id,
            "taskId": task_id,
            "page": 1 if page is None else page,
            "pageSize": 50 if page_size is None else page_size,
        }
        if endpoint_id is not None:
            payload["endpointId"] = endpoint_id
        data = await self._request(
            "POST",
            f"{_NETWORK_PROBE_PREFIX}/fingerprints/query",
            json=payload,
        )
        return self._scan_result("network-probe", "fingerprints", data, task_id=task_id)

    async def control_network_probe(
        self,
        session_id: str,
        task_id: str,
        action: str,
    ) -> dict[str, object]:
        endpoints = {
            "pause": "pause",
            "resume": "resume",
            "stop": "stop",
            "delete": "delete",
        }
        endpoint = endpoints.get(action)
        if endpoint is None:
            raise LeoAIError("tool_input_invalid", "unsupported network probe action")
        data = await self._action_request(
            "POST",
            f"{_NETWORK_PROBE_PREFIX}/{endpoint}",
            json={"sessionId": session_id, "taskId": task_id},
        )
        return self._scan_result("network-probe", action, data, task_id=task_id)

    async def list_database_dialects(self) -> dict[str, object]:
        data = await self._request("GET", "/puppet-node/sql/dialects")
        return self._database_result("dialects", data)

    async def database_connection_query(
        self,
        operation: str,
        endpoint: str,
        session_id: str,
        connection_id: str,
    ) -> dict[str, object]:
        if self._protocol_profile == "1x" and operation == "runtime_capabilities":
            raise LeoAIError(
                "leoai_capability_unsupported",
                "LeoAI 1.x does not expose database runtime capabilities",
            )
        data = await self._request(
            "POST",
            endpoint,
            json=self._database_payload(session_id, connection_id),
        )
        return self._database_result(operation, data)

    async def database_object_query(
        self,
        operation: str,
        endpoint: str,
        session_id: str,
        connection_id: str,
        object_ref: dict[str, object],
    ) -> dict[str, object]:
        payload = self._database_payload(session_id, connection_id)
        payload.update(self._database_object_payload(object_ref))
        data = await self._request("POST", endpoint, json=payload)
        return self._database_result(operation, data)

    async def query_database_table(
        self,
        session_id: str,
        connection_id: str,
        table: dict[str, object],
        *,
        page: int,
        page_size: int,
        columns: list[str],
        order_by: list[dict[str, object]],
        filters: list[dict[str, object]],
        include_total: bool,
        query_timeout_seconds: int,
    ) -> dict[str, object]:
        payload = self._database_payload(session_id, connection_id)
        payload.update(self._database_object_payload(table))
        payload.update(
            {
                "page": page,
                "pageSize": page_size,
                "columns": columns,
                "orderBy": order_by,
                "filters": filters,
            }
        )
        if self._protocol_profile == "2x":
            payload.update(
                {
                    "includeTotal": include_total,
                    "queryTimeoutSeconds": query_timeout_seconds,
                }
            )
        data = await self._request("POST", "/puppet-node/sql/data/query-table", json=payload)
        return self._database_result("table_query", data)

    async def database_action(
        self,
        operation: str,
        endpoint: str,
        session_id: str,
        connection_id: str,
        *,
        object_ref: dict[str, object] | None = None,
        values: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = self._database_payload(session_id, connection_id)
        if object_ref is not None:
            payload.update(self._database_object_payload(object_ref))
        if values is not None:
            payload.update(values)
        data = await self._action_request("POST", endpoint, json=payload)
        return self._database_result(operation, data)

    async def start_file_upload(
        self,
        session_id: str,
        vfs_path: str,
        file_path: str,
        chunk_size: int,
    ) -> dict[str, object]:
        data = await self._action_request(
            "POST",
            "/puppet-node/file/upload-engine/start",
            json={
                "sessionId": session_id,
                "vfsPath": vfs_path,
                "filePath": file_path,
                "chunkSize": chunk_size,
            },
        )
        return self._file_transfer_start_result("upload", data)

    async def start_file_download(
        self,
        session_id: str,
        file_path: str,
        threads: int,
        chunk_size: int,
    ) -> dict[str, object]:
        data = await self._action_request(
            "POST",
            "/puppet-node/file/download-engine/start",
            json={
                "sessionId": session_id,
                "filePath": file_path,
                "threads": threads,
                "chunkSize": chunk_size,
            },
        )
        return self._file_transfer_start_result("download", data)

    async def query_file_transfer(
        self,
        direction: str,
        task_id: str,
    ) -> dict[str, object]:
        data = await self._request(
            "POST",
            f"/puppet-node/file/{direction}-engine/progress",
            json={"taskId": task_id},
        )
        return self._file_transfer_result(direction, "queried", data, task_id=task_id)

    async def control_file_transfer(
        self,
        direction: str,
        session_id: str,
        task_id: str,
        action: str,
    ) -> dict[str, object]:
        if action not in {"pause", "resume", "cancel", "retry", "remove"}:
            raise LeoAIError("tool_input_invalid", "unsupported file-transfer action")
        if self._protocol_profile == "1x" and action == "remove":
            raise LeoAIError(
                "leoai_capability_unsupported",
                "LeoAI 1.x does not expose file-transfer task removal",
            )
        payload: dict[str, object] = {"taskId": task_id}
        if action in {"resume", "retry"}:
            payload = {"sessionId": session_id, "taskId": task_id}
        data = await self._action_request(
            "POST",
            f"/puppet-node/file/{direction}-engine/{action}",
            json=payload,
        )
        return self._file_transfer_result(direction, action, data, task_id=task_id)

    async def list_file_transfer_tasks(
        self,
        direction: str,
        session_id: str,
    ) -> dict[str, object]:
        data = await self._request(
            "POST",
            f"/puppet-node/file/{direction}-engine/tasks",
            json={"sessionId": session_id},
        )
        return self._file_transfer_result(direction, "listed", data)

    async def invoke_allowed_plugin(
        self,
        session_id: str,
        plugin_id: str,
        plugin_param: dict[str, object],
    ) -> dict[str, object]:
        if plugin_id not in self._allowed_plugin_ids:
            raise LeoAIError("tool_disabled", "plugin is not enabled by the deployment allowlist")
        data = await self._action_request(
            "POST",
            "/puppet-node/plugin/invoke",
            json={
                "sessionId": session_id,
                "pluginId": plugin_id,
                "pluginParam": plugin_param,
            },
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid plugin result")
        return {
            "untrusted_external_content": True,
            "plugin": {
                "pluginId": plugin_id,
                "result": _sanitize_external(data),
            },
        }

    async def get_docker_info(self, session_id: str) -> dict[str, object]:
        return await self._docker_query("info", "/puppet-node/docker/info", {"sessionId": session_id})

    async def list_docker_containers(self, session_id: str, include_stopped: bool) -> dict[str, object]:
        return await self._docker_query(
            "containers",
            "/puppet-node/docker/list-containers",
            {"sessionId": session_id, "all": include_stopped},
        )

    async def list_docker_images(self, session_id: str) -> dict[str, object]:
        return await self._docker_query(
            "images",
            "/puppet-node/docker/list-images",
            {"sessionId": session_id},
        )

    async def list_docker_networks(self, session_id: str) -> dict[str, object]:
        return await self._docker_query(
            "networks",
            "/puppet-node/docker/list-networks",
            {"sessionId": session_id},
        )

    async def inspect_docker_container(self, session_id: str, container_id: str) -> dict[str, object]:
        return await self._docker_query(
            "inspect",
            "/puppet-node/docker/inspect",
            {"sessionId": session_id, "containerId": container_id},
        )

    async def get_docker_container_logs(
        self,
        session_id: str,
        container_id: str,
        tail: int,
    ) -> dict[str, object]:
        return await self._docker_query(
            "logs",
            "/puppet-node/docker/logs",
            {"sessionId": session_id, "containerId": container_id, "tail": tail},
        )

    async def exec_in_docker_container(
        self,
        session_id: str,
        container_id: str,
        command: str,
    ) -> dict[str, object]:
        return await self._system_action(
            "docker_exec",
            container_id,
            "/puppet-node/docker/exec",
            {"sessionId": session_id, "containerId": container_id, "cmd": command},
        )

    async def control_docker_container(
        self,
        session_id: str,
        container_id: str,
        action: str,
        stop_timeout_seconds: int,
    ) -> dict[str, object]:
        endpoints = {
            "start": "/puppet-node/docker/start",
            "stop": "/puppet-node/docker/stop",
            "restart": "/puppet-node/docker/restart",
            "pause": "/puppet-node/docker/pause",
            "unpause": "/puppet-node/docker/unpause",
        }
        endpoint = endpoints.get(action)
        if endpoint is None:
            raise LeoAIError("tool_input_invalid", "unsupported Docker container action")
        payload: dict[str, object] = {
            "sessionId": session_id,
            "containerId": container_id,
        }
        if action in {"stop", "restart"}:
            payload["timeout"] = stop_timeout_seconds
        return await self._system_action(
            f"docker_container_{action}",
            container_id,
            endpoint,
            payload,
        )

    async def remove_docker_container(
        self,
        session_id: str,
        container_id: str,
        force: bool,
    ) -> dict[str, object]:
        return await self._system_action(
            "docker_container_removed",
            container_id,
            "/puppet-node/docker/remove-container",
            {"sessionId": session_id, "containerId": container_id, "force": force},
        )

    async def remove_docker_image(
        self,
        session_id: str,
        image_id: str,
        force: bool,
    ) -> dict[str, object]:
        return await self._system_action(
            "docker_image_removed",
            image_id,
            "/puppet-node/docker/remove-image",
            {"sessionId": session_id, "imageId": image_id, "force": force},
        )

    def _validate_file_content(self, content: str) -> None:
        if len(content.encode("utf-8")) > self._max_file_write_bytes:
            raise LeoAIError(
                "tool_input_too_large",
                "file content exceeded the configured UTF-8 byte limit",
            )

    async def _file_action(
        self,
        operation: str,
        path: str,
        endpoint: str,
        payload: dict[str, object],
        *,
        destination_path: str | None = None,
    ) -> dict[str, object]:
        data = await self._action_request("POST", endpoint, json=payload)
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid file action result")
        action: dict[str, object] = {
            "operation": operation,
            "path": path,
            "result": _sanitize_external(data),
        }
        if destination_path is not None:
            action["destinationPath"] = destination_path
        return {"untrusted_external_content": True, "fileAction": action}

    async def _action_request(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self._request(
            method,
            path,
            retry_on_auth_expiry=False,
            **kwargs,
        )

    async def _system_query(
        self,
        result_name: str,
        endpoint: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        data = await self._request("POST", endpoint, json=payload)
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid system query result")
        return {
            "untrusted_external_content": True,
            result_name: _sanitize_external(data),
        }

    async def _docker_query(
        self,
        operation: str,
        endpoint: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        data = await self._request("POST", endpoint, json=payload)
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid Docker query result")
        return {
            "untrusted_external_content": True,
            "docker": {
                "operation": operation,
                "result": _sanitize_external(data),
            },
        }

    def _database_result(self, operation: str, data: object) -> dict[str, object]:
        if not isinstance(data, (dict, list)):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid database result")
        return {
            "untrusted_external_content": True,
            "database": {
                "operation": operation,
                "result": _sanitize_external(data),
            },
        }

    def _file_transfer_start_result(self, direction: str, data: object) -> dict[str, object]:
        if not isinstance(data, dict) or not isinstance(data.get("taskId"), str) or not data["taskId"]:
            raise LeoAIError("leoai_protocol_error", "LeoAI returned a file-transfer start without a task ID")
        return self._file_transfer_result(direction, "started", data)

    def _file_transfer_result(
        self,
        direction: str,
        operation: str,
        data: object,
        *,
        task_id: str | None = None,
    ) -> dict[str, object]:
        if not isinstance(data, (dict, list)):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid file-transfer result")
        task: dict[str, object] = {
            "direction": direction,
            "operation": operation,
            "result": _sanitize_external(data),
        }
        if task_id is not None:
            task["taskId"] = task_id
        return {"untrusted_external_content": True, "fileTransfer": task}

    def _database_payload(self, session_id: str, connection_id: str) -> dict[str, object]:
        return {
            "sessionId": session_id,
            "connection": {"connectionId": connection_id},
        }

    def _database_object_payload(self, object_ref: dict[str, object]) -> dict[str, object]:
        if self._protocol_profile == "2x":
            return {"objectRef": object_ref}
        payload: dict[str, object] = {}
        catalog = object_ref.get("catalog")
        schema = object_ref.get("schema")
        if catalog is not None and schema is not None and catalog != schema:
            raise LeoAIError(
                "tool_input_invalid",
                "LeoAI 1.x cannot represent different catalog and schema values",
            )
        namespace = schema or catalog
        if namespace is not None:
            payload["database"] = namespace
        if object_ref.get("name") is not None:
            payload["table"] = object_ref["name"]
        return payload

    async def _system_action(
        self,
        operation: str,
        target: str,
        endpoint: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        data = await self._action_request("POST", endpoint, json=payload)
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid system action result")
        return {
            "untrusted_external_content": True,
            "systemAction": {
                "operation": operation,
                "target": target,
                "result": _sanitize_external(data),
            },
        }

    def _scan_result(
        self,
        scan_type: str,
        operation: str,
        data: object,
        *,
        task_id: str | None = None,
    ) -> dict[str, object]:
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid scan result")
        task: dict[str, object] = {
            "scanType": scan_type,
            "operation": operation,
            "result": _sanitize_external(data),
        }
        if task_id is not None:
            task["taskId"] = task_id
        return {"untrusted_external_content": True, "scanTask": task}

    def _scan_start_result(self, scan_type: str, data: object) -> dict[str, object]:
        if not isinstance(data, dict) or not isinstance(data.get("taskId"), str) or not data["taskId"]:
            raise LeoAIError("leoai_protocol_error", "LeoAI returned a scan start without a task ID")
        return self._scan_result(scan_type, "started", data, task_id=data["taskId"])

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



def _disguise_summary(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid disguise")
    return _pick(value, "disguiseId", "disguiseName", "version", "description", "remark")


def _generated_artifact(kind: str, value: Any, *, payload_key: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid generated artifact")
    content = _artifact_content(value)
    if content is None:
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an empty generated artifact")
    artifact: dict[str, object] = {
        "kind": kind,
        "content": content,
    }
    artifact.update(_pick(value, "fileExtension", "mediaType", "warnings"))
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {
            key: item
            for key, item in value.items()
            if key not in _ARTIFACT_RESERVED_KEYS
        }
    sanitized = _sanitize_external(metadata) if metadata else {}
    if isinstance(sanitized, dict):
        sanitized.pop("payloadKey", None)
        sanitized.pop("payload_key", None)
        if sanitized:
            artifact["metadata"] = sanitized
    class_artifacts = value.get("classArtifacts")
    if isinstance(class_artifacts, dict):
        artifact["classArtifactNames"] = [str(name) for name in class_artifacts]
    if payload_key:
        artifact["payloadKey"] = payload_key
    return {"untrusted_external_content": True, "artifact": artifact}


_PAYLOAD_KEY_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_PAYLOAD_KEY_LENGTH = 8


def _resolve_payload_key(payload_key: str | None) -> str:
    if payload_key is not None:
        value = payload_key.strip()
        if value:
            return value
    return "".join(secrets.choice(_PAYLOAD_KEY_ALPHABET) for _ in range(_PAYLOAD_KEY_LENGTH))


_ARTIFACT_CONTENT_KEYS = ("content", "shell", "code")
_ARTIFACT_RESERVED_KEYS = {
    "classArtifacts",
    "code",
    "content",
    "fileExtension",
    "mediaType",
    "metadata",
    "shell",
    "warnings",
}


def _artifact_content(value: dict[str, Any]) -> str | None:
    for key in _ARTIFACT_CONTENT_KEYS:
        content = value.get(key)
        if isinstance(content, str) and content:
            return content
    return None


def _packer_names(value: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(name: object) -> None:
        text = str(name)
        if text and text not in seen:
            seen.add(text)
            names.append(text)

    if isinstance(value, dict):
        groups = value.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if isinstance(group, dict) and isinstance(group.get("packers"), list):
                    for packer in group["packers"]:
                        add(packer)
        ungrouped = value.get("ungrouped")
        if isinstance(ungrouped, list):
            for packer in ungrouped:
                add(packer)
    elif isinstance(value, list):
        for item in value:
            add(item)
    return names


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


def _profile_from_root_listing(value: Any) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("fileList"), list):
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid root directory listing")
    roots = []
    for item in value["fileList"]:
        if not isinstance(item, dict) or item.get("isDirectory") is not True:
            continue
        path = item.get("path")
        if isinstance(path, str) and path and path not in roots:
            roots.append(path)
    if not roots:
        absolute_path = value.get("absolutePath")
        if isinstance(absolute_path, str) and absolute_path:
            roots.append(absolute_path)
    if not roots:
        raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid root directory listing")
    windows = any(len(root) >= 3 and root[0].isalpha() and root[1] == ":" and root[2] in {"/", "\\"} for root in roots)
    return {
        "osFamily": "WINDOWS" if windows else "POSIX",
        "pathStyle": "WINDOWS" if windows else "POSIX",
        "separator": "\\" if windows else "/",
        "caseSensitivity": "INSENSITIVE" if windows else "SENSITIVE",
        "roots": roots,
    }


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
        or normalized.startswith("header")
        or normalized.startswith("proxy")
        or normalized.endswith("disguiseid")
        or normalized.endswith("strategy")
    )
