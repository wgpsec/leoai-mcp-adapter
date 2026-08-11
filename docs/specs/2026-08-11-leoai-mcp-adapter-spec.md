# LeoAI MCP Adapter 设计

## 状态

- 状态：Implemented，待真实 LeoAI 与 PoJun Docker Runtime 集成验证
- 日期：2026-08-11
- 仓库：`leoai-mcp-adapter`
- 上游：[cha0upup/LeoAI](https://github.com/cha0upup/LeoAI)
- 调研基线：LeoAI `main` commit `6fb4de979db23de4fa8b23e5ed6a98c710a82fda`
- 目标客户端：PoJun Docker Runtime Backend，也允许其他标准 MCP Client
- 核心决策：独立 HTTP MCP adapter，不维护带 MCP 功能的 LeoAI 整仓 fork

## 1. 决策摘要

新建一个独立的 `leoai-mcp-adapter` 服务，将明确允许的 LeoAI REST API
转换为 Streamable HTTP MCP Tools：

```text
PoJun Docker Agent
    -> PoJun task-scoped HTTP MCP relay
    -> leoai-mcp-adapter
    -> LeoAI REST API
    -> LeoAI Puppet Session
```

Adapter 不复制 LeoAI 业务实现，不直接依赖 LeoAI Java module，不调用 LeoAI
内置 Platform AI 或 Puppet AI。PoJun 继续负责 OODA、Agent session、模型调用、
Finding/Evidence 和任务恢复；LeoAI 只作为受权限控制的后渗透能力提供方。

第一阶段只开放小规模、显式列举的查询能力。命令执行、文件修改、凭据提取、
脚本与插件、代理隧道、持久化和其他高影响能力不进入首版。

## 2. 背景与问题

LeoAI 已提供覆盖项目、Puppet、在线会话、基础信息、文件、扫描和系统管理的
Spring REST Controller，但当前没有 MCP Server。它的 Web API 使用登录接口和
`JSESSIONID` Cookie 认证，并通过用户、团队、Puppet 和 Session 权限进行隔离。

直接在 LeoAI 中增加 MCP module 或长期维护整仓 fork 会带来以下问题：

- 需要持续合并活跃上游的大量 Java、前端、数据库和 AI 变更；
- MCP 容易绕过现有 Controller 权限与审计边界，直接触达 service；
- 把 LeoAI Agent 暴露给 PoJun 会形成两套自主循环，破坏任务可观测性；
- PoJun Docker 镜像需要额外 Java 运行时或 LeoAI 依赖，增加发布成本。

LeoAI 已有 REST API，因此最小方案是在进程边界外做协议适配，并复用其现有
认证、权限和审计。

## 3. 目标与非目标

### 3.1 目标

1. 让 PoJun Docker Runtime 通过标准 HTTP MCP 使用受控的 LeoAI 查询能力。
2. 不修改 PoJun Agent 镜像，不要求在 Task 容器中安装 Java 或 LeoAI。
3. 不修改 LeoAI 业务实现，优先兼容正式上游 release。
4. 保留 LeoAI 用户、团队、Puppet、Session 权限及操作审计。
5. 保护 LeoAI 密码和 Cookie，不向 Agent 返回或透传。
6. 对输出大小、超时、并发、错误和日志实施明确边界。
7. 用契约测试及时发现 LeoAI API 漂移，而不是静默产生错误结果。

### 3.2 非目标

- 不实现新的后渗透平台或 Puppet Runtime；
- 不把 LeoAI Platform AI/Puppet AI 作为 PoJun 子 Agent；
- 不提供可访问任意 URL、HTTP method 或 LeoAI path 的通用代理工具；
- 不在首版支持命令执行、文件写入、上传、删除、凭据提取或持久化；
- 不绕过 LeoAI 权限，不读取 LeoAI SQLite 数据库；
- 不把 MCP adapter 当作强隔离或 Prompt Injection 防护边界；
- 不在首版建设多租户数据库、用户同步或独立权限管理系统。

## 4. 架构边界

### 4.1 独立服务

Adapter 使用 Python 3.11+，基于官方 MCP Python SDK 提供 Streamable HTTP
transport，使用 `httpx` 调用 LeoAI。依赖必须锁定版本，不允许运行时从网络
安装或解析 `latest`。

Adapter 可以作为独立容器部署在 LeoAI 同一受控网络，也可以作为本机服务部署。
默认只监听 `127.0.0.1`；需要跨主机访问时，必须放在 TLS reverse proxy 后，
不得明文暴露到公网。

不使用 `mcp-proxy`。本服务本身就是 HTTP MCP Server，不存在 stdio 转换需求。

### 4.2 单信任域

首版每个 adapter 实例只连接一个 LeoAI base URL，并只使用一个专用 LeoAI
服务账号。该实例服务于一个明确的 PoJun 信任域，不在同一进程中混合多个组织
或多个 LeoAI 凭据。

这意味着首版不声称实现 PoJun 用户到 LeoAI 用户的一一映射。需要不同权限域时，
部署不同 adapter 实例并使用不同的 LeoAI 服务账号。该约束避免为了首版新增
凭据数据库、租户路由和复杂身份委派。

### 4.3 PoJun 接入

PoJun 将 adapter 注册为 Super Admin 管理的 HTTP MCP。配置形态为：

```json
{
  "type": "http",
  "url": "https://leoai-mcp.internal.example/mcp",
  "headers": {
    "Authorization": "Bearer <adapter-client-token>"
  }
}
```

静态 adapter token 由 PoJun Worker 的现有 HTTP MCP relay 持有。Task 容器中的
Agent 只看到 task-scoped relay URL/token，不能读取 adapter token 或 LeoAI
Cookie。该 MCP 不允许普通项目参数覆盖 URL、headers 或 LeoAI 凭据。

## 5. 认证与凭据

### 5.1 Adapter 客户端认证

首版使用一个高熵 Bearer Token 保护 `/mcp`。Token 只从环境变量或只读 secret
file 加载，不支持命令行明文参数，不记录原文。健康检查分为：

- `/healthz`：只表示进程存活，不需要认证，不探测 LeoAI；
- `/readyz`：需要 adapter client token，验证配置和最近一次 LeoAI 会话状态，不返回凭据。

Bearer Token 比较使用恒定时间比较。认证失败只返回统一 `401`，不得泄露配置、
LeoAI 地址或 token prefix。

### 5.2 LeoAI 认证

当前 LeoAI 调研基线使用：

- `POST /platform/user/login` 建立 `JSESSIONID`；
- `/platform/**` 与 `/puppet-node/**` 由 `LoginInterceptor` 校验会话；
- Controller/PermissionService 继续校验用户、团队、Puppet 和 Session 权限。

首版 adapter 使用专用、低权限 LeoAI 服务账号登录，Cookie 只保存在进程内存，
不写日志、workspace、响应或磁盘。遇到 `401` 时，在互斥锁内重新登录一次并只
重试确定为查询语义的请求；连续失败进入 not-ready，不循环撞库。

LeoAI 上游目前无法提供 API Token，因此首版固定使用部署者手动提供的账号密码。
手动提供只发生在 adapter 启动或受控配置阶段，不通过 MCP Tool 参数传入，也不
允许 Agent 修改或轮换凭据。生产环境仍推荐使用专用低权限账号，禁止使用 LeoAI
默认管理员账号。

账号密码变更通过更新 secret file/运行环境并重启或受控 reload adapter 完成；
adapter 不提供远程改密、凭据回显或凭据测试接口。启动时登录失败应进入 not-ready，
不得反复尝试撞库。

### 5.3 配置

首版配置只使用环境变量/secret file，不新增数据库：

| 配置 | 必需 | 默认 | 说明 |
|---|---:|---|---|
| `LEOAI_BASE_URL` | 是 | 无 | 固定 LeoAI origin，不允许 Tool 覆盖 |
| `LEOAI_USERNAME` | 是 | 无 | 专用低权限账号 |
| `LEOAI_PASSWORD_FILE` | 是 | 无 | 部署者手动提供的只读密码文件；不接受明文 CLI 参数 |
| `MCP_CLIENT_TOKEN_FILE` | 是 | 无 | PoJun 到 adapter 的 Bearer Token |
| `ADAPTER_ENV` | 否 | `production` | 生产默认启用安全配置校验 |
| `LEOAI_TLS_VERIFY` | 否 | `true` | 生产禁止关闭 |
| `LEOAI_CONNECT_TIMEOUT_SECONDS` | 否 | `5` | 连接超时 |
| `LEOAI_READ_TIMEOUT_SECONDS` | 否 | `30` | 普通查询超时 |
| `MCP_MAX_CONCURRENCY` | 否 | `8` | 实例并发上限 |
| `MCP_MAX_RESPONSE_BYTES` | 否 | `1048576` | 单次 LeoAI 响应上限 |
| `MCP_ENABLE_FILE_READ` | 否 | `false` | 文件读取能力显式 opt-in |
| `MCP_MAX_FILE_BYTES` | 否 | `262144` | 单次文件读取上限，最大不超过 2 MiB |
| `MCP_BIND_HOST` | 否 | `127.0.0.1` | Adapter 监听地址 |
| `MCP_BIND_PORT` | 否 | `8000` | Adapter 监听端口 |
| `MCP_ALLOWED_HOSTS` | 否 | localhost | DNS rebinding Host allowlist |

启动时校验 base URL scheme、secret file 权限和数值边界。生产模式拒绝 HTTP、
`LEOAI_TLS_VERIFY=false`、空 token、默认密码和不可解析配置。

## 6. MCP Tool 契约

### 6.1 设计原则

- 每个 Tool 对应一个固定 LeoAI endpoint，不接受任意 URL/path/method；
- Tool 名称稳定，不直接复制 LeoAI Controller 名称；
- 输入使用严格 schema，拒绝未知字段、空字符串和超长值；
- 输出为结构化 JSON，保留必要业务字段，移除 Cookie、headers、堆栈和凭据；
- LeoAI 返回的页面、文件、摘要和主机内容统一标记为不可信数据；
- Tool annotations 只用于客户端体验，不能代替服务端权限。

本 spec 中的“只读”是指不向目标 Puppet 主机发起修改操作。LeoAI 某些查询
Controller 仍可能更新服务端缓存、Session 活跃时间或审计记录；这些副作用必须
在工具描述和审计中明确，不能伪装成完全无副作用的查询。

### 6.2 第一阶段默认工具

| MCP Tool | LeoAI API | 语义 |
|---|---|---|
| `leo_list_projects` | `GET /platform/projects` | 列出服务账号可见项目 |
| `leo_list_project_puppets` | `GET /platform/projects/{projectId}/puppets` | 列出项目根 Puppet |
| `leo_list_sessions` | `GET /platform/session/sessions` | 列出可见在线/缓存 Session，可按 projectId 过滤 |
| `leo_get_session_capabilities` | `POST /puppet-node/capabilities` | 获取 Session 能力和 Runtime Profile |
| `leo_get_current_host` | `POST /puppet-node/current-host` | 获取当前 Puppet 信息 |
| `leo_get_basic_info` | `POST /puppet-node/basic-info` | 获取基础主机信息 |
| `leo_get_recon_summary` | `POST /platform/session/recon-summary` | 读取已有侦察摘要 |
| `leo_get_file_profile` | `POST /puppet-node/file/profile` | 获取目标文件系统语义 |
| `leo_list_files` | `POST /puppet-node/file/list` | 列举指定目录，不读取内容 |

Puppet/Project 等对象必须经过 adapter 字段白名单投影。默认禁止返回 `connLink`、
`headers`、proxy 配置、disguise/strategy 配置、内部用户标识和任何凭据字段；
只返回 Tool 为完成任务所需的 id、名称、层级、协议、类型、权限和状态摘要。

所有 Session Tool 只接受已存在的 `sessionId`。首版不自动调用
`GET /puppet-node/init` 创建实时 Session，因为建连会改变 LeoAI 运行状态并可能
触发目标通信。用户先在 LeoAI 中建立会话，或由未来明确授权的 action tool 建立。

### 6.3 可选文件读取

只有 `MCP_ENABLE_FILE_READ=true` 时注册 `leo_read_file`：

- 对应 `POST /puppet-node/file/preview-chunk`；
- 输入为 `sessionId`、`path`、`offset`、`maxBytes`；
- `maxBytes` 受 adapter 全局上限约束，不能由 Agent 提升；
- 响应返回 base64、总大小、offset、nextOffset 和 truncated；
- 不自动拼接读取整文件，不自动解码二进制，不写本地文件；
- 文件内容明确标记为 `untrusted_external_content=true`。

文件读取虽然不修改目标，但可能暴露凭据并携带提示词注入，因此默认关闭。

### 6.4 明确不开放的首版能力

- `/platform/ai/**`、`/puppet-node/ai/**`；
- `/puppet-node/command/**`；
- 文件 edit/new/upload/delete/move/copy/compress/decompress；
- 凭据、浏览器数据、数据库写入；
- 脚本、Java Class、插件和内存马生成；
- 代理、端口转发、反向隧道和持久化；
- 防火墙、服务、进程、账户、计划任务和容器修改；
- 通用 `leo_request`、`leo_invoke` 或透传 Controller 参数的工具。

后续任何 action tool 必须单独设计不可由 Agent 伪造的授权凭证。MCP 参数中的
`confirmed=true`、自然语言确认或 Tool annotation 都不是有效安全门禁。

## 7. LeoAI Client 行为

### 7.1 响应解析

LeoAI 主要返回 `ApiResponse` envelope。Client 必须同时检查 HTTP status 和
业务 `code`，不能因 HTTP 200 就判定成功。缺字段、类型变化或非 JSON 响应映射为
`leoai_protocol_error`，不能把 HTML 登录页当作业务结果。

Adapter 不向 MCP 客户端返回 LeoAI 原始异常栈。标准错误至少包括：

- `leoai_auth_failed`
- `leoai_permission_denied`
- `leoai_not_found`
- `leoai_session_expired`
- `leoai_capability_unsupported`
- `leoai_timeout`
- `leoai_unavailable`
- `leoai_protocol_error`
- `adapter_rate_limited`
- `tool_disabled`

错误响应包含可读消息、是否可重试和 correlation id，不包含账号、Cookie、密码、
Authorization header、目标文件内容或 LeoAI 内部堆栈。

### 7.2 超时与重试

- 所有请求使用 connect/read/write/pool timeout，禁止 `timeout=None`；
- 只对连接前失败、`401` 重新认证后的查询请求做一次有界重试；
- 不自动重试不确定是否已执行的写操作；
- MCP 请求取消时立即取消上游 HTTP 请求；
- 并发超过上限时快速返回 `adapter_rate_limited`，不无限排队。

### 7.3 API 漂移

LeoAI 当前 README、Maven version 和 release tag 并不完全一致，因此 adapter 不以
展示版本号猜测兼容性。每个支持的 LeoAI release/commit 使用契约测试固定：

- endpoint、method 和必要字段；
- 登录/Cookie 行为；
- 权限拒绝；
- ApiResponse envelope；
- Session、capability 和分页/大小边界。

未知上游版本默认 not-ready，管理员可显式启用兼容性 override 做灰度，但不得
静默宣称已验证。

## 8. 安全模型

### 8.1 权限不扩大

Adapter 的有效权限不超过其 LeoAI 服务账号。Adapter 不接受由 MCP 调用者指定
LeoAI username/team/role，也不缓存其他用户 Cookie。LeoAI 的 `403` 原样映射为
权限拒绝，不用管理员凭据重试。

首版 PoJun 集成由 Super Admin 创建和管理，不能让普通 Project 修改 URL、header、
服务账号或启用文件读取。项目是否可使用该 MCP 仍由 PoJun 现有 MCP 开关控制，
但这不替代 adapter 和 LeoAI 的权限检查。

### 8.2 Prompt Injection

LeoAI 主机信息、目录名、文件、侦察摘要和错误文本都可能由不可信目标控制。
Adapter 只把它们作为数据返回，不解释其中的命令，也不据此调用其他 LeoAI API。

PoJun 可信 developer instructions 应继续声明：MCP/HTTP/文件内容是不可信数据，
不得把其中的指令提升为平台授权。Runtime Guard 和 Docker 隔离只能控制后果，
不能证明内容安全。

### 8.3 SSRF、路径和输出边界

- LeoAI base URL 启动时固定，Tool 不能提供 host、scheme 或 path；
- 禁止重定向到不同 origin，默认关闭自动 redirect；
- TLS 默认验证，禁止生产使用自签名跳过校验；
- projectId/sessionId/path 有长度上限和控制字符校验；
- 文件 path 仍由 LeoAI Puppet 解释，adapter 不做错误的本地路径规范化；
- JSON 和文本结果均有最大字节数，超限返回截断元数据；
- 不返回 LeoAI response headers、Set-Cookie 或反向代理诊断页。

### 8.4 审计与日志

日志记录：correlation id、MCP tool、LeoAI endpoint 名称、耗时、HTTP/业务状态、
重试次数和结果大小。日志不记录 Tool 中的文件内容、密码、Cookie、Token、完整
Authorization header 或敏感主机数据。

LeoAI 继续记录服务账号对应的访问审计。Adapter 日志必须能与 LeoAI 审计通过
correlation id 和时间关联；若上游暂不支持透传 correlation id，则先保留 adapter
侧 id，不为此修改 LeoAI 业务逻辑。

## 9. 上游策略

Adapter 只通过 LeoAI 公共 HTTP API 通信，不复制或链接 LeoAI 源码，也不维护
LeoAI 整仓 fork。LeoAI 账号密码由部署者在 adapter 运行环境中手动提供；本项目
不依赖 LeoAI API Token。

LeoAI API 或响应结构变化时，通过 contract fixtures 固定兼容范围并更新 adapter
的 API 映射，不直接修改 LeoAI 业务内核。

## 10. TDD 实施顺序

1. 建立 Python package、锁定依赖和最小 `/healthz`，先写启动配置失败测试。
2. 用 mock HTTP server 写登录、Cookie 内存保存、401 单次重登和凭据脱敏测试。
3. 写 ApiResponse envelope、HTTP/业务错误映射、timeout、cancel 和输出上限测试。
4. 实现 MCP Bearer 认证与 Streamable HTTP initialize/list-tools，验证未认证拒绝。
5. 逐个用 contract fixture 实现默认只读 Tools；每个 Tool 先有输入、权限和错误测试。
6. 增加并发上限、取消传播、连接池回收和 shutdown 测试。
7. 增加 Prompt Injection canary，确认恶意主机名/摘要/目录仅作为数据返回。
8. 增加 secret scan 测试，确认密码、Cookie、Token 不进入 MCP 响应和日志。
9. 在本地 LeoAI 测试实例执行真实兼容矩阵：登录、项目、Session、capability、
   basic info、recon summary、目录列表和权限拒绝。
10. 在 PoJun Docker Runtime 中注册为 HTTP MCP，验证 Claude-compatible 与 Codex
    的 tools/list、tools/call、超时、取消、OODA continuation 和任务结束清理。
11. 最后才实现默认关闭的 `leo_read_file`，覆盖大小限制、分页、二进制和注入内容。

## 11. 验收标准

当前已通过本地配置、LeoAI mock contract 与标准 MCP Streamable HTTP 客户端
测试。真实 LeoAI 权限矩阵、PoJun Docker Runtime、Claude-compatible/Codex 和
OODA continuation 回放仍是发布前验证项，不因本地测试通过而视为已完成。

- PoJun Docker Runtime 无需修改 Agent 镜像即可连接 adapter；
- 未认证 MCP 请求全部拒绝，且错误不泄露配置；
- Agent 永远看不到 LeoAI password、Cookie 或 adapter client token；
- 默认 Tool 列表不包含任何高影响写操作和 LeoAI AI Agent；
- 服务账号无权访问的项目、Puppet、Session 不能通过 adapter 访问；
- LeoAI 401 只触发一次有界重登，不形成登录风暴；
- 所有网络请求有 timeout，客户端取消能回收上游请求；
- 未知 LeoAI 响应结构 fail-closed 为 protocol error；
- 恶意摘要、主机名和目录内容只能作为不可信数据返回；
- 真实 PoJun OODA、Playwright、context1337、其他 MCP 和 Agent session 行为不变；
- adapter 停用或删除 MCP 配置后，不影响 PoJun 其他 Backend 与 Project。

## 12. 发布与回滚

1. 先在测试 LeoAI 创建专用低权限账号，只授予测试项目/Puppet 可见性。
2. 部署 adapter 到 LeoAI 邻近网络，默认只开启 metadata/session 查询工具。
3. 在 PoJun 创建仅测试 Project 可用的 HTTP MCP 配置，通过 relay 保存静态 token。
4. 完成真实 Docker 回放和审计对账后，再扩大 MCP 可见范围。
5. 文件读取保持默认关闭，单独灰度并检查凭据与 Prompt Injection 风险。
6. 回滚只需禁用 PoJun MCP 或停止 adapter；不修改 LeoAI 数据和 PoJun OODA 状态。

## 13. 后续阶段门槛

以下能力不能直接在首版上追加，必须先形成独立安全设计：

- 创建/连接 LeoAI Session；
- 命令执行与交互式终端；
- 文件写入、上传、删除和下载落盘；
- 凭据提取、数据库、脚本、插件和内存马生成；
- 端口扫描、HTTP Fuzz、代理、隧道和持久化；
- 多 PoJun 用户到多 LeoAI 身份的委派；
- 人工批准或 PoJun 签发的不可伪造 action grant。

进入高影响阶段前至少需要：明确授权主体、目标范围、动作 scope、过期时间、
幂等键、审计关联、取消语义和 replay 防护。缺少其中任何一项时保持只读。

## 14. 开放事项

- 在部署环境中创建并手动交付专用低权限 LeoAI 账号密码；
- 在真实 LeoAI release 上冻结第一组 contract fixtures；
- 高影响 Tool 不属于本 spec 的实施范围，需要另行评审。
