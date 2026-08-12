# leoai-mcp-adapter

这是一个面向 [LeoAI](https://github.com/cha0upup/LeoAI) 的独立 Streamable HTTP
MCP 适配器。它为 PoJun 等客户端提供范围受限、显式映射的 Tool，不嵌入 LeoAI、
不复制其 Agent 循环，也不提供任意 HTTP 转发。默认的 `observe` Profile 只提供
只读能力；显式启用的 `operate` Profile 额外提供带审计的 Session、终端、文件、
系统、Docker、受限扫描、结构化数据库和文件传输操作。

`2x` Profile 面向 LeoAI `main` 分支 commit
`6fb4de979db23de4fa8b23e5ed6a98c710a82fda` 的 API；`1x` Profile 面向官方
`1.0.1` Release。当前已经完成本地协议与契约测试，以及 LeoAI `1.0.1` Java/PHP
Puppet 的真实 Observe、Session、终端、文件、进程、服务、网络和扫描矩阵验证。
`1x` Profile 的 SQLite 元数据、查询、插入、更新和删除映射也已在真实 `1.0.1`
环境通过；终端的 Base64 输出解码后与 canary 一致。
真实 Java Puppet 已通过主机、端口和指纹扫描；Recon 请求已到达 LeoAI，但新生成的
Java Puppet 返回了上游 `requestId` 不匹配。这属于 LeoAI Runtime 兼容性缺口，
Adapter 不会为此降级。PoJun Docker Runtime 验证仍是环境级发布门禁。

发布版 `LeoAi-2.0.0.jar` 已通过 Adapter 登录、Tool discovery、Project/Session、
基础信息、进程、服务、网络和终端真实回放。使用该 JAR 自带生成器生成的 protocol-v2
PHP Puppet 后，文件 Profile/CRUD、PTY 终端、SQLite runtime-capabilities 以及 2x
`objectRef` 元数据、查询、插入、更新和删除完整矩阵均通过。测试机原有的旧 Java
Puppet 存在 action 编码不兼容且缺少 SQLite JDBC Driver，属于 Server/Puppet 混合
版本与目标运行时依赖问题；升级到 2.0.0 后应重新生成或确保 Puppet 协议版本匹配。

Adapter 支持两个显式的上游协议 Profile。连接官方 LeoAI `1.0.1` 时设置
`LEOAI_PROTOCOL_PROFILE=1x`；连接当前 2.x API 时设置
`LEOAI_PROTOCOL_PROFILE=2x`。默认值为 `2x`，以保持现有部署行为不变。一个进程在
整个生命周期内只绑定一个 Profile；需要同时连接两个 LeoAI 版本时，应运行两个相互
独立的 Adapter 实例。Adapter 不猜测版本，也不会在操作失败后换用另一套协议重试。

LeoAI `1.0.1` 不包含新版 Project API。在该版本上，`leo_list_projects` 和
`leo_list_project_puppets` 会返回 `leoai_capability_unsupported`，Session 和文件
Tool 仍可使用。缺少显式文件系统 Profile 接口时，Adapter 会通过旧版只读根目录列表
接口推导路径语义。仍要求首次修改密码的账号会被报告为未就绪，必须先在 LeoAI 中
完成改密才能使用。

## Tool

默认注册以下 Tool：

- `leo_list_projects`
- `leo_list_project_puppets`
- `leo_list_sessions`
- `leo_get_session_capabilities`
- `leo_get_current_host`
- `leo_get_basic_info`
- `leo_get_recon_summary`
- `leo_get_file_profile`
- `leo_list_files`

只有设置 `MCP_ENABLE_FILE_READ=true` 时才会注册 `leo_read_file`。默认的 `observe`
Profile 不暴露命令执行、文件写入或其他操作能力。

设置 `MCP_TOOL_PROFILE=operate` 后，还会注册以下能力：

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
- 使用 LeoAI 已保存连接的数据库方言、能力、元数据和表查询 Tool
- 结构化数据库连接测试、行插入、更新和删除 Tool，不提供原始 SQL Tool
- 有边界的上传/下载启动、查询、控制和任务列表 Tool
- 使用固定 Endpoint 的 Docker 信息、列表、检查、日志、执行、控制和删除 Tool

当 `MCP_ALLOWED_PLUGIN_IDS` 非空时，`operate` 还会注册
`leo_invoke_allowed_plugin`。调用的 ID 必须位于部署侧允许列表中，并且对应 LeoAI
中已经安装的插件。Adapter 不会创建、上传或动态加载插件。

扫描启动和控制属于 Action，LeoAI 登录过期后不会重放。扫描查询可以重新认证并重试
一次。指纹和侦察目标只接受结构化 HTTP 或 TCP 格式；指纹与侦察扫描要求 Java
Puppet 支持组件调用。

数据库 Tool 只接受已保存的 `connectionId`，凭据和任意连接字符串不能作为 MCP
输入。文件上传来源必须是不能包含父级穿越的 LeoAI VFS 相对路径；下载只返回任务
元数据，不返回下载文件内容。数据库修改、传输启动/控制和插件调用都属于 Action，
认证过期后不会重放；元数据和任务查询可以重新认证并重试一次。

未来的 `privileged` Profile 预留给需要独立设计的高风险能力。Adapter 永远不会暴露
通用请求转发。

## 配置

日常运行推荐使用仓库内的 TOML 示例。服务地址、账号、LeoAI 密码和 MCP 客户端
Token 都可以写在同一个配置文件中，不作为 MCP Tool 参数传入：

```bash
cp adapter.example.toml adapter.toml
chmod 600 adapter.toml
```

然后只需编辑 `adapter.toml`。包含内联密码和 Token 时，Adapter 强制要求配置文件
权限为 `0600`；未知字段或同一凭据同时使用内联值与文件路径时会拒绝启动。生成的
`adapter.toml` 已加入 Git 忽略规则。

列表项 `mcp_allowed_hosts` 和 `mcp_allowed_plugin_ids` 在 TOML 中使用字符串数组。
原有 Secret 文件与环境变量方式仍然支持，适合容器或编排系统；启动时二选一，使用
`--config` 后配置完全来自 TOML，不与环境变量隐式合并。若部署时不希望在 TOML
内联凭据，仍可使用 `leoai_password_file` 和 `mcp_client_token_file`，相对路径按 TOML
所在目录解析。

本地可信网络若不想维护 Host 白名单，可以设置：

```toml
mcp_dns_rebinding_protection = false
```

这会接受任意 Host，但 Bearer Token 认证仍然生效。公网或不可信网络建议保持默认值
`true`，并通过 `mcp_allowed_hosts` 明确允许访问 Adapter 的域名或 IP。

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `LEOAI_BASE_URL` | 必填 | 固定的 LeoAI Origin |
| `LEOAI_USERNAME` | 必填 | 专用的低权限 LeoAI 账号 |
| `LEOAI_PASSWORD_FILE` | 必填 | 保存 LeoAI 密码的 Secret 文件 |
| `MCP_CLIENT_TOKEN_FILE` | 必填 | 保存 Bearer Token 的 Secret 文件 |
| `ADAPTER_ENV` | `production` | 可选 `production`、`development` 或 `test` |
| `LEOAI_TLS_VERIFY` | `true` | 验证 LeoAI TLS；生产环境必须为 `true` |
| `LEOAI_CONNECT_TIMEOUT_SECONDS` | `5` | 上游连接超时 |
| `LEOAI_READ_TIMEOUT_SECONDS` | `30` | 上游读取超时 |
| `LEOAI_PROTOCOL_PROFILE` | `2x` | 固定的上游契约，可选 `1x` 或 `2x` |
| `MCP_MAX_CONCURRENCY` | `8` | 超限时快速失败的上游并发上限 |
| `MCP_MAX_RESPONSE_BYTES` | `1048576` | LeoAI 响应体最大字节数 |
| `MCP_TOOL_PROFILE` | `observe` | 部署侧 Profile，可选 `observe`、`operate` 或 `privileged` |
| `MCP_ENABLE_FILE_READ` | `false` | 是否注册可选的文件读取 Tool |
| `MCP_MAX_FILE_BYTES` | `262144` | 单次可选文件读取的最大字节数 |
| `MCP_MAX_FILE_WRITE_BYTES` | `262144` | 单次创建/编辑文件允许的最大 UTF-8 字节数 |
| `MCP_ALLOWED_PLUGIN_IDS` | 空 | 允许调用的已安装插件 ID，使用英文逗号分隔 |
| `MCP_BIND_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `MCP_BIND_PORT` | `8000` | HTTP 监听端口 |
| `MCP_DNS_REBINDING_PROTECTION` | `true` | 是否校验 HTTP Host；设为 `false` 可显式全部放开 |
| `MCP_ALLOWED_HOSTS` | 仅本机 | 允许的 HTTP Host，使用英文逗号分隔 |

生产模式拒绝明文 HTTP LeoAI URL，也不允许关闭 TLS 验证。只有在受控的本地 LeoAI
部署中才能使用 `ADAPTER_ENV=development`。

## 运行

```bash
uv sync --locked
uv run leoai-mcp-adapter --config adapter.toml
```

不传 `--config` 时仍按原方式读取环境变量：

```bash
uv run leoai-mcp-adapter
```

服务 Endpoint：

- `GET /healthz`：无需认证，只表示 Adapter 进程存活
- `GET /readyz`：需要 Bearer 认证，验证 LeoAI Session 是否就绪
- `/mcp`：需要 Bearer 认证的 Streamable HTTP MCP

使用反向代理或容器部署时，设置 `MCP_BIND_HOST=0.0.0.0`，并将
`MCP_ALLOWED_HOSTS` 显式配置为外部实际使用的 Host。TLS 终止层和 Adapter 都应位于
受控网络中。

PoJun MCP 注册示例：

```json
{
  "type": "http",
  "url": "https://leoai-mcp.internal.example/mcp",
  "headers": {
    "Authorization": "Bearer <adapter-client-token>"
  }
}
```

仓库同时提供轻量项目技能
[`use-leoai-mcp`](skills/use-leoai-mcp/SKILL.md)，用于约束 Puppet/Session 选择、
先观察后操作、Action 后独立验证、终端输出解码和资源清理。PoJun 新建 Project 时可
直接上传该 `SKILL.md`，阶段范围建议选择 `bootstrap + explore`。Skill 不包含凭据、
服务地址或 Tool 实现，仍需为 Project 单独启用上述 LeoAI HTTP MCP。

构建可选容器镜像：

```bash
docker build -t leoai-mcp-adapter:0.1.0 .
```

运行镜像时，应将两个 Secret 文件以只读方式挂载。容器使用非特权用户运行，不包含
LeoAI 或 Java Runtime。

## 验证

```bash
uv run pytest
uv run ruff check .
```

信任模型、API 映射、发布门禁和明确排除的能力，参见
[设计规格](docs/specs/2026-08-11-leoai-mcp-adapter-spec.md)。
