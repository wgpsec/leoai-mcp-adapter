# leoai-mcp-adapter

A standalone Streamable HTTP MCP adapter for
[LeoAI](https://github.com/cha0upup/LeoAI). It exposes a fixed set of bounded
query tools for clients such as PoJun without embedding LeoAI, forking its Agent
loop, or providing arbitrary HTTP forwarding.

The implementation targets the LeoAI `main` API at commit
`6fb4de979db23de4fa8b23e5ed6a98c710a82fda`. Local protocol and contract tests,
plus a live LeoAI `1.0.1` PHP Puppet session matrix, are complete. PoJun Docker
Runtime validation remains an environment-level release gate.

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
file writes, credential extraction, plugins, persistence, proxy/tunnel actions,
LeoAI AI endpoints, and generic request forwarding are not exposed.

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
| `MCP_MAX_CONCURRENCY` | `8` | Fast-fail concurrent query limit |
| `MCP_MAX_RESPONSE_BYTES` | `1048576` | Maximum LeoAI response bytes |
| `MCP_ENABLE_FILE_READ` | `false` | Register the optional file read tool |
| `MCP_MAX_FILE_BYTES` | `262144` | Maximum bytes per optional file read |
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
