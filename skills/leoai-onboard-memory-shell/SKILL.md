---
name: leoai-onboard-memory-shell
description: Place a LeoAI Java memory shell on an authorized execution point, register a reachable Puppet, and open a verifiable Session. Use when the task is LeoAI 上线, memory-shell onboarding, or turning a Jeecg/Spring/Tomcat RCE into a managed Puppet.
---

# LeoAI 内存壳上线

MCP 只生成制品并登记已可达 `connLink`，不负责投递。生成器吐出的 Injector 在 JDK 17 / Spring Boot 3 上经常挂不上请求链。完成标准是 Session 还在 `leo_list_sessions` 里，并能读到 `leo_get_basic_info`。

## 完成标准

必须拿到工具返回的真实值：Leo `projectId`、`puppetId`、`sessionId`。缺一即未完成。不要把生成制品、创建项目、报表报错或 HTTP 200 当成上线。

## 流程

1. 确认 `leo_list_disguises`、`leo_list_shell_generator_types`、`leo_generate_memory_shell`、`leo_add_puppet`、`leo_open_session`、`leo_list_sessions` 存在。缺工具就停，报告缺口。
2. 用授权数据包探测运行时，再生成。不要先生成再猜。
3. 先按生成器 Injector 投递。投完必须用同一对 header 验证壳已挂上。
4. 未挂上则走 [JDK 17 / Spring Boot 3 回退](references/jdk17-spring-boot3.md)，禁止假登记。
5. 壳已挂上后再 `leo_add_puppet`，然后 `leo_open_session` → `leo_list_sessions` → `leo_get_basic_info`。

## 探测

用授权执行点回显，不要看日志猜。至少确认：

- `java.version`。9+ 必须传 `targetJavaVersion=17+`（或 `9+`）。
- `javax.servlet.Servlet` vs `jakarta.servlet.Servlet`。只有 jakarta 时传 `servletNamespace=jakarta`。`auto` 会变成 javax。
- 容器：`TomcatEmbeddedWebappClassLoader` / `LaunchedClassLoader` 视为嵌入式 Tomcat + Spring Boot。优先 `SpringWebMVC` + `InterceptorInjector`，不要先打 Tomcat Filter。
- 当前请求能否取到 `DispatcherServlet.CONTEXT`。

把探测结果 `throw new RuntimeException(String.valueOf(...))` 回显。表达式成功但没抛错时，后续业务失败（例如 JDBC）只说明 Groovy 跑过，不说明壳已挂上。

## 生成

`http` / `httpchunk` 必须传同一对 `headerName` / `headerValue`，登记时原样给 `leo_add_puppet`。
`payloadKey` 是 LeoAI 2.2.0 的 PayloadCodec AES 密钥。生成时可不传，用返回的 `artifact.payloadKey`；`leo_add_puppet` 必须带同一把，不能换。

建议：

- `reqDisguiseId` / `respDisguiseId`：用 `leo_list_disguises` 的真实 id。2.2.0 常见 `inner_Java_Base64_1.0.0`，不要写死已删除的 `inner_AESBin_1.0.0`
- `packerType`：`GroovyClassLoaderDefiner`
- `protocol`：`http`。不要传 `httpchunk`（LeoAI 侧是 `httpChunked`）。
- JDK 9+：`targetJavaVersion=17+`
- Boot 3 / Tomcat 10：`servletNamespace=jakarta`
- `byPassJavaModule=true` 不够。它自己也要 `setAccessible(Unsafe)`，JDK 17 上常是空操作。

## 投递约束

若执行点是 QLExpress / `groovy.util.Eval`：

- 外层用 `=use groovy.util.Eval; Eval.me('GROOVY')`。
- `Eval.me` 参数里禁止出现 `=`、`+`、`'`。`=` 会被吃掉，`+` 会变空格。
- 字节码用 URL-safe Base64，去掉 padding。Java `Base64.getUrlDecoder()` 允许无 padding。
- 字符串拼接用 GString 或 `concat`，不要用 `+`。
- 用闭包参数代替赋值：`{x -> ...}(value)`。Groovy 里 `fn(true) { ... }` 会被当成多参数调用，`setAccessible(true)` 后用分号再写闭包。

`GroovyClassLoader.defineClass(name, bytes)` 是 public，可用来加载 Injector / Interceptor。不要对 `ClassLoader.defineClass` 做 `setAccessible`。

## 验证壳

对同一 `connLink` 发请求，带上生成时的 header：

- 未挂上：仍是原应用 JSON / HTML。
- 已挂上：变成 AES/Base64 密文，或明显不再走原业务。

未挂上禁止 `leo_add_puppet`。Injector `newInstance()` 成功、类已加载、报表 JDBC 失败，都不等于 Filter/Interceptor 已进入请求链。

## 登记与复核

- `connLink` 必须是 DispatcherServlet 能打到的 URL，通常就是这条 RCE 路径。
- `headers` 由 MCP 从 `headerName`/`headerValue` 组装，不要自己造 Cookie。
- `leo_open_session` 失败原文若是「无主机回复」，回到投递/验证，不要换 disguise 硬登记。
- 成功后再 `leo_list_sessions` 确认 session 仍在，并用 `leo_get_basic_info` 读到真实主机信息。
- 上线成功后写过程文档：运行时判断、生成参数、header 验证、投递路径，以及真实 `projectId` / `puppetId` / `sessionId`。未拿到这些 id 时不要用文档充数。不要写凭据、MCP token、Leo 密码。
