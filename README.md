# leoai-mcp-adapter

A standalone Streamable HTTP MCP adapter for
[LeoAI](https://github.com/cha0upup/LeoAI). It exposes bounded, explicitly mapped
tools for clients such as PoJun without embedding LeoAI, forking its Agent loop,
or providing arbitrary HTTP forwarding. The default `observe` profile is
read-only; the opt-in `operate` profile adds audited Session, terminal, file,
system, Docker, bounded scan, structured database, and file-transfer actions.

The implementation targets the LeoAI `main` API at commit
`6fb4de979db23de4fa8b23e5ed6a98c710a82fda`. Local protocol and contract tests,
plus live LeoAI `1.0.1` Java and PHP Puppet observe, Session/terminal/file, and
process/service/network/scan matrices, are complete. The live Java Puppet passed
host, port, and fingerprint scans; recon reached LeoAI but the generated Java
Puppet returned an upstream `requestId` mismatch, which remains a LeoAI runtime
compatibility gap rather than an adapter fallback.
PoJun Docker Runtime validation remains an environment-level release gate.

LeoAI `1.0.1` does not contain the newer Project API. On that release,
`leo_list_projects` and `leo_list_project_puppets` return
`leoai_capability_unsupported`; the Session and File tools remain available.
The adapter derives filesystem path semantics from the older root-list API when
the explicit profile endpoint is absent. Accounts that still require their
initial password change are reported as not ready and must be updated in LeoAI
before use.

## Tools

The default tool set is:

- `leo_list_projects`
- `leo_list_project_puppets`
- `leo_list_sessions`
- `leo_get_session_capabilities`
- `leo_get_current_host`
- `leo_get_basic_info`
- `leo_get_recon_summary`
- `leo_get_file_profile`
- `leo_list_files`

`leo_read_file` is registered only when `MCP_ENABLE_FILE_READ=true`. Commands,
file writes, and other actions are not exposed by the default `observe` profile.

Set `MCP_TOOL_PROFILE=operate` to additionally register:

- `leo_open_session` / `leo_close_session`
- `leo_open_terminal` / `leo_write_terminal` / `leo_read_terminal` / `leo_stop_terminal`
- `leo_create_file` / `leo_edit_file` / `leo_create_directory`
- `leo_move_file` / `leo_copy_file` / `leo_delete_file`
- `leo_list_processes` / `leo_find_processes` / `leo_kill_process`
- `leo_list_services` / `leo_query_service` / `leo_control_service`
- `leo_list_network_connections` / `leo_get_network_connection_summary`
- `leo_check_host_reachability`
- `leo_start_port_scan` / `leo_query_port_scan` / `leo_control_port_scan`
- `leo_start_fingerprint_scan` / `leo_query_fingerprint_scan` / `leo_control_fingerprint_scan`
- `leo_start_recon_scan` / `leo_query_recon_scan` / `leo_control_recon_scan`
- database dialect/capability/metadata/table-query tools using saved LeoAI connections
- structured database row test/insert/update/delete tools; no raw SQL tool
- bounded upload/download start/query/control/list task tools
- Docker info/list/inspect/logs/exec/control/remove tools with fixed endpoints

When `MCP_ALLOWED_PLUGIN_IDS` is nonempty, `operate` also registers
`leo_invoke_allowed_plugin`. The ID must be present in the deployment-side
allowlist and refer to a plugin already installed in LeoAI. The adapter never
creates, uploads, or dynamically loads plugins.

Scan starts and controls are actions and are never replayed after an expired
LeoAI login. Scan queries may reauthenticate and retry once. Fingerprint and
recon targets accept only structured HTTP or TCP forms; fingerprint and recon
scans require a Java Puppet with component invocation support.
Database tools accept only a saved `connectionId`; credentials and arbitrary
connection strings are not MCP inputs. File upload sources are relative LeoAI
VFS paths without parent traversal, and downloads return task metadata rather
than downloaded file contents. Database changes, transfer starts/controls, and
plugin invocations are actions and are never replayed after authentication
expiry; metadata and task queries may retry once.

The future `privileged` profile is reserved for separately designed high-impact
capabilities. Generic request forwarding is never exposed.

## Configuration

LeoAI credentials and the MCP client token are read from `0600` secret files.
They are never MCP tool arguments.

| Variable | Default | Purpose |
|---|---|---|
| `LEOAI_BASE_URL` | required | Fixed LeoAI origin |
| `LEOAI_USERNAME` | required | Dedicated low-privilege LeoAI account |
| `LEOAI_PASSWORD_FILE` | required | LeoAI password secret file |
| `MCP_CLIENT_TOKEN_FILE` | required | Bearer token secret file |
| `ADAPTER_ENV` | `production` | `production`, `development`, or `test` |
| `LEOAI_TLS_VERIFY` | `true` | Verify LeoAI TLS; production requires `true` |
| `LEOAI_CONNECT_TIMEOUT_SECONDS` | `5` | Upstream connect timeout |
| `LEOAI_READ_TIMEOUT_SECONDS` | `30` | Upstream read timeout |
| `MCP_MAX_CONCURRENCY` | `8` | Fast-fail concurrent upstream call limit |
| `MCP_MAX_RESPONSE_BYTES` | `1048576` | Maximum LeoAI response bytes |
| `MCP_TOOL_PROFILE` | `observe` | `observe`, `operate`, or `privileged` deployment-side profile |
| `MCP_ENABLE_FILE_READ` | `false` | Register the optional file read tool |
| `MCP_MAX_FILE_BYTES` | `262144` | Maximum bytes per optional file read |
| `MCP_MAX_FILE_WRITE_BYTES` | `262144` | Maximum UTF-8 bytes per file create/edit action |
| `MCP_ALLOWED_PLUGIN_IDS` | empty | Comma-separated installed plugin IDs allowed for invocation |
| `MCP_BIND_HOST` | `127.0.0.1` | HTTP bind address |
| `MCP_BIND_PORT` | `8000` | HTTP bind port |
| `MCP_ALLOWED_HOSTS` | localhost only | Comma-separated HTTP Host allowlist |

Production mode rejects plain HTTP LeoAI URLs and disabled TLS verification.
Use `ADAPTER_ENV=development` only for a controlled local LeoAI deployment.

## Run

```bash
uv sync --locked
uv run leoai-mcp-adapter
```

Endpoints:

- `GET /healthz`: unauthenticated process liveness only
- `GET /readyz`: Bearer-authenticated LeoAI session readiness
- `/mcp`: Bearer-authenticated Streamable HTTP MCP

For a reverse proxy or container, set `MCP_BIND_HOST=0.0.0.0` and explicitly
set `MCP_ALLOWED_HOSTS` to the externally used host names. Keep TLS termination
and the adapter on a controlled network.

Example PoJun MCP registration:

```json
{
  "type": "http",
  "url": "https://leoai-mcp.internal.example/mcp",
  "headers": {
    "Authorization": "Bearer <adapter-client-token>"
  }
}
```

Build the optional container image with:

```bash
docker build -t leoai-mcp-adapter:0.1.0 .
```

Mount both secret files read-only when running the image. The container runs as
an unprivileged user and does not include LeoAI or a Java runtime.

## Verify

```bash
uv run pytest
uv run ruff check .
```

See [the design spec](docs/specs/2026-08-11-leoai-mcp-adapter-spec.md) for the
trust model, API mapping, release gates, and excluded capabilities.
