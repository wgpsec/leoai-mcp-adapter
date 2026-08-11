from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

from .client import LeoAIClient
from .errors import LeoAIError


class LeoAITools:
    def __init__(
        self,
        client: LeoAIClient,
        *,
        max_concurrency: int,
        max_file_bytes: int,
        max_file_write_bytes: int,
        allowed_plugin_ids: tuple[str, ...],
    ) -> None:
        self._client = client
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
            if error.code != "leoai_capability_unsupported":
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
    ) -> dict[str, object]:
        request = self._request if command_type == "read" else self._action_request
        data = await request(
            "POST",
            "/puppet-node/command/exec-command",
            json={
                "sessionId": session_id,
                "processId": terminal_id,
                "cmd": command,
                "type": command_type,
            },
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

    async def check_host_reachability(
        self,
        session_id: str,
        hosts: list[str],
        timeout_ms: int,
    ) -> dict[str, object]:
        data = await self._action_request(
            "POST",
            "/puppet-node/host-reachable/scan",
            json={
                "sessionId": session_id,
                "scanHosts": hosts,
                "scanTimeout": timeout_ms,
            },
        )
        if not isinstance(data, dict):
            raise LeoAIError("leoai_protocol_error", "LeoAI returned an invalid host reachability result")
        return {
            "untrusted_external_content": True,
            "hostReachability": _sanitize_external(data),
        }

    async def start_port_scan(
        self,
        session_id: str,
        host: str,
        ports: list[int],
        timeout_ms: int,
        threads: int,
    ) -> dict[str, object]:
        data = await self._action_request(
            "POST",
            "/puppet-node/port-scan/start-scan",
            json={
                "sessionId": session_id,
                "scanHost": host,
                "scanPorts": ports,
                "scanTimeout": timeout_ms,
                "threadsNum": threads,
            },
        )
        return self._scan_start_result("port", data)

    async def query_port_scan(self, session_id: str, task_id: str) -> dict[str, object]:
        return await self._query_scan(
            "port",
            "/puppet-node/port-scan",
            session_id,
            task_id,
        )

    async def control_port_scan(
        self,
        session_id: str,
        task_id: str,
        action: str,
    ) -> dict[str, object]:
        return await self._control_scan(
            "port",
            "/puppet-node/port-scan",
            session_id,
            task_id,
            action,
        )

    async def start_fingerprint_scan(
        self,
        session_id: str,
        fingerprint_id: str,
        targets: list[dict[str, object]],
        threads: int,
    ) -> dict[str, object]:
        data = await self._action_request(
            "POST",
            "/puppet-node/fingerprint/start-scan",
            json={
                "sessionId": session_id,
                "fingerprintId": fingerprint_id,
                "targets": targets,
                "threads": threads,
            },
        )
        return self._scan_start_result("fingerprint", data)

    async def query_fingerprint_scan(self, session_id: str, task_id: str) -> dict[str, object]:
        return await self._query_scan(
            "fingerprint",
            "/puppet-node/fingerprint",
            session_id,
            task_id,
        )

    async def control_fingerprint_scan(
        self,
        session_id: str,
        task_id: str,
        action: str,
    ) -> dict[str, object]:
        return await self._control_scan(
            "fingerprint",
            "/puppet-node/fingerprint",
            session_id,
            task_id,
            action,
        )

    async def start_recon_scan(
        self,
        session_id: str,
        targets: list[dict[str, object]],
        rule_selector: dict[str, object] | None,
        threads: int,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "sessionId": session_id,
            "targets": targets,
            "threads": threads,
        }
        if rule_selector is not None:
            payload["ruleSelector"] = rule_selector
        data = await self._action_request(
            "POST",
            "/puppet-node/recon-scan/start-scan",
            json=payload,
        )
        return self._scan_start_result("recon", data)

    async def query_recon_scan(self, session_id: str, task_id: str) -> dict[str, object]:
        return await self._query_scan(
            "recon",
            "/puppet-node/recon-scan",
            session_id,
            task_id,
        )

    async def control_recon_scan(
        self,
        session_id: str,
        task_id: str,
        action: str,
    ) -> dict[str, object]:
        return await self._control_scan(
            "recon",
            "/puppet-node/recon-scan",
            session_id,
            task_id,
            action,
        )

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
        payload["objectRef"] = object_ref
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
        data = await self._request(
            "POST",
            "/puppet-node/sql/data/query-table",
            json={
                "sessionId": session_id,
                "connection": {"connectionId": connection_id},
                "objectRef": table,
                "page": page,
                "pageSize": page_size,
                "columns": columns,
                "orderBy": order_by,
                "filters": filters,
                "includeTotal": include_total,
                "queryTimeoutSeconds": query_timeout_seconds,
            },
        )
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
            payload["objectRef"] = object_ref
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
        return self._scan_result(scan_type, "started", data)

    async def _query_scan(
        self,
        scan_type: str,
        endpoint_prefix: str,
        session_id: str,
        task_id: str,
    ) -> dict[str, object]:
        data = await self._request(
            "POST",
            f"{endpoint_prefix}/query-result",
            json={"sessionId": session_id, "taskId": task_id},
        )
        return self._scan_result(scan_type, "queried", data, task_id=task_id)

    async def _control_scan(
        self,
        scan_type: str,
        endpoint_prefix: str,
        session_id: str,
        task_id: str,
        action: str,
    ) -> dict[str, object]:
        endpoints = {
            "pause": "pause-scan",
            "resume": "resume-scan",
            "stop": "stop-scan",
        }
        endpoint = endpoints.get(action)
        if endpoint is None:
            raise LeoAIError("tool_input_invalid", "unsupported scan action")
        data = await self._action_request(
            "POST",
            f"{endpoint_prefix}/{endpoint}",
            json={"sessionId": session_id, "taskId": task_id},
        )
        return self._scan_result(scan_type, action, data, task_id=task_id)

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
        or normalized.startswith("proxy")
        or normalized.endswith("disguiseid")
        or normalized.endswith("strategy")
    )
