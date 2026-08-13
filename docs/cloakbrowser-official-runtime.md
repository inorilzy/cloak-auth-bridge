# CloakBrowser 官方运行时与长期会话方案

> 调查日期：2026-08-12。一手来源：CloakBrowser 官网、CloakHQ 官方仓库/源码、PyPI、npm、Docker Hub 和 Playwright 官方文档。

## 结论

1. 截图中的 **GitHub / PyPI / npm / Docker 是同一个 CloakBrowser 的发行与使用渠道，不是四套并列的浏览器控制库**。它们共享同一个定制 Chromium 二进制和同一组 stealth launch args。官网自己将实现描述为“定制 Chromium 上的薄 wrapper”：每次由 Playwright 或 Puppeteer 启动该二进制。[官网 How it works](https://cloakbrowser.dev/)
2. **Python 当前底层就是 stock Playwright**，不是自有 binding、Camoufox 或当前的 Patchright。Python metadata 直接依赖 `playwright>=1.40`；源码调用 `sync_playwright` / `async_playwright` 的 `chromium.launch()` 和 `chromium.launch_persistent_context()`。[官方 `pyproject.toml`](https://github.com/CloakHQ/CloakBrowser/blob/main/pyproject.toml#L49-L53) [官方 Python launch 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/cloakbrowser/browser.py#L289-L406) [官方 Python persistent 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/cloakbrowser/browser.py#L533-L666)
3. **Node 默认后端是 `playwright-core`**；`cloakbrowser/puppeteer` 是可选的 `puppeteer-core` 入口。npm 只把两者声明为 optional peer dependencies，并共用同一 CloakBrowser Chromium。[官方 npm metadata](https://github.com/CloakHQ/CloakBrowser/blob/main/js/package.json#L59-L80) [Playwright wrapper](https://github.com/CloakHQ/CloakBrowser/blob/main/js/src/playwright.ts#L171-L218) [Puppeteer wrapper](https://github.com/CloakHQ/CloakBrowser/blob/main/js/src/puppeteer.ts#L147-L190)
4. 对当前 `cloak-browser-auth` 的最佳主路径仍是：**独立 Holder 唯一持有 `launch_persistent_context_async()`，MCP 只做可重连客户端**。数据面优先用 Playwright `browser.bind()` / `browser_type.connect()`；官方 `cloakserve` 作为 CDP 兼容出口，不宜取代本地 Windows 可见、持久 profile 的主 Holder。

## 截图四项分别是什么

| 入口 | 实际性质 | 包含内容 / 用法 |
|---|---|---|
| GitHub | 源码仓库、文档、wrapper 发布源和旧版 Chromium binary releases | Python/JS/.NET wrapper 源码、Dockerfile、`bin/cloakserve`、签名的 Chromium release。[官方仓库](https://github.com/CloakHQ/CloakBrowser) [Releases](https://github.com/CloakHQ/CloakBrowser/releases) |
| PyPI | Python wrapper 发行渠道 | `pip install cloakbrowser`；安装 Python API 和 Playwright 依赖，首次运行下载对应平台的 CloakBrowser Chromium。[PyPI 官方 metadata](https://pypi.org/pypi/cloakbrowser/json) |
| npm | Node/TypeScript wrapper 发行渠道 | `npm install cloakbrowser playwright-core` 是默认 Playwright API；也可安装 `puppeteer-core` 并从 `cloakbrowser/puppeteer` 导入。[官方 npm 包源码](https://github.com/CloakHQ/CloakBrowser/blob/main/js/package.json) [npm registry](https://www.npmjs.com/package/cloakbrowser) |
| Docker | 预装好运行时的容器镜像 | 内含 Python wrapper、Node wrapper、预下载 Chromium、Xvfb、`cloaktest` 和 `cloakserve`。`docker run --rm ... cloaktest` 只是一次性快速测试；需要服务时用 detached `cloakserve`。[官方 Dockerfile](https://github.com/CloakHQ/CloakBrowser/blob/main/Dockerfile) [Docker Hub](https://hub.docker.com/r/cloakhq/cloakbrowser) |

因此，截图并没有表明“CloakBrowser 不用 Playwright”。它表明的是同一项目同时提供 Python、Node 和容器化发行。

## 确切技术栈

```text
Python cloakbrowser wrapper
  -> stock Playwright Python
  -> patched CloakBrowser Chromium

Node cloakbrowser (default)
  -> playwright-core
  -> patched CloakBrowser Chromium

Node cloakbrowser/puppeteer (optional)
  -> puppeteer-core
  -> the same patched CloakBrowser Chromium

cloakserve (Docker-first remote mode)
  -> Python aiohttp/websockets CDP multiplexer
  -> directly spawned patched Chromium with --remote-debugging-port
  <- Playwright/Puppeteer/other CDP client
```

- CloakBrowser 的差异点主要在 **Chromium C++ 源码补丁 + wrapper 选定的 executable/args + 可选 humanize wrapper**，不是新造了一套页面自动化 API。[官方 README](https://github.com/CloakHQ/CloakBrowser#how-it-works)
- Patchright 只是历史过渡：Changelog 显示 0.3.0 曾改用 Patchright，0.3.9 因代理认证和 `add_init_script` 问题切回 stock Playwright；当前 `main` 已明确对 `backend=` 抛错，说明 Patchright 不再支持。[官方 Changelog](https://github.com/CloakHQ/CloakBrowser/blob/main/CHANGELOG.md) [当前 Python 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/cloakbrowser/browser.py#L270-L276)
- Camoufox 是 Firefox 系的另一项目，CloakBrowser 官方自述为定制 Chromium，两者没有依赖关系。[官方 FAQ](https://github.com/CloakHQ/CloakBrowser#faq)

## 官方公开 API

### Python

- `launch()` / `launch_async()` 返回标准 Playwright `Browser`。
- `launch_context()` / `launch_context_async()` 返回非持久 `BrowserContext`；wrapper 将 `context.close()` 包装为同时关闭 browser。
- `launch_persistent_context()` / `launch_persistent_context_async()` 使用指定 `user_data_dir`，返回该浏览器唯一的 persistent context；cookies、localStorage、cache 等跨重启保留。CloakBrowser wrapper 为它的 `close()` 增加了 `pw.stop()` 清理，所以关掉 owner context 就是关掉整个 owner。[官方 API 说明](https://github.com/CloakHQ/CloakBrowser#api) [Python 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/cloakbrowser/browser.py)

### Node

- 默认 Playwright 入口导出 `launch` / `launchContext` / `launchPersistentContext`，另有 `buildLaunchOptions` / `buildContextOptions` / `humanizeBrowser` 供组合集成。[官方 exports](https://github.com/CloakHQ/CloakBrowser/blob/main/js/src/index.ts)
- Puppeteer 入口导出 `launch` 和传入 `userDataDir` 的 `launchPersistentContext`（返回 Puppeteer `Browser`）。[官方 Puppeteer 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/js/src/puppeteer.ts#L193-L244)

### Connect / reattach

CloakBrowser wrapper **没有导出自有 `connect()` API**。官方的外部连接方案是：

1. 启动时传 `--remote-debugging-port`，再由其他框架通过 CDP 连接；或
2. 在官方 Docker 镜像运行 `cloakserve`，客户端用 Playwright `chromium.connect_over_cdp()` 连接。[官方 Framework integrations](https://github.com/CloakHQ/CloakBrowser#framework-integrations) [官方 Docker CDP server mode](https://github.com/CloakHQ/CloakBrowser#cdp-server-mode)

## `cloakserve` 的真实生命周期

`cloakserve` 是官方 `bin/` 下的 Python CDP multiplexer，Dockerfile 把它复制到 `/usr/local/bin`。它不调用 Playwright launch，而是用 `subprocess.Popen` 直接启动 CloakBrowser Chromium，并在一个公共端口上转发 CDP HTTP/WebSocket。[官方 `cloakserve` 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/bin/cloakserve)

- 同一 `fingerprint` seed 在进程存活时会复用同一 Chrome 进程，首次连接的 launch params 生效。
- CDP client 断开时只减少 refcount。`idle_timeout` 默认为 `0`，因此 **client/MCP 断开默认不关 Chrome**，后续可重新连接。
- 显式配置 `--idle-timeout=N` 后，最后一个 client 断开 N 秒才回收 Chrome。
- `cloakserve` 自身停止时，`on_shutdown` 会终止其所有 Chrome。它不是“丢下 Chrome 后自己可以随便退出”的 launcher。
- 默认 pool 在 cleanup 时会删除 seed 的 `user_data_dir`；官方 README 也明说 idle cleanup 会删除该临时 profile。所以 **`cloakserve` seed 不等于可靠的长期持久 auth profile**。跨 Holder/Docker 重启的登录态应使用 `launch_persistent_context(user_data_dir)` 并保留该目录。[进程/refcount/cleanup 源码](https://github.com/CloakHQ/CloakBrowser/blob/main/bin/cloakserve#L143-L440) [官方 README 的 idle cleanup 说明](https://github.com/CloakHQ/CloakBrowser#cdp-server-mode)

注意：当前源码 `terminate_seed()` 的 docstring 提到一个会保留 profile 的“persistent pool override”，但当前 `bin/cloakserve` 没有这个子类，实际 `_cleanup_process()` 仍调用 `_safe_rmtree()`。架构不应依赖该注释，应以实际代码和 README 的“temporary profile removed”为准。

## 对当前 `cloak-browser-auth` 的推荐

### 主方案：独立 persistent Holder + Playwright 原生 endpoint

```text
Windows user session
└─ Cloak Holder (unique owner, long-lived)
   ├─ cloakbrowser.launch_persistent_context_async(profile_dir, headless=False)
   ├─ context.browser.bind(...) -> named pipe / loopback WS
   ├─ small authenticated management plane: ensure/status/close
   └─ exits only on explicit close, manual browser close, or crash

MCP (restartable client)
├─ playwright.chromium.connect(endpoint)
├─ page/network/console/debugger/CDP-session tools
└─ detach = disconnect client only; never context.close()
```

理由：

- 它直接复用 CloakBrowser 的官方 Python persistent API，不会丢掉 cookies、localStorage、IndexedDB、cache 和扩展数据。
- Playwright 1.59+ 的 `browser.bind()` 可将已启动 browser 绑定到 named pipe 或 WebSocket，多个客户端再用 `browser_type.connect()` 连接。当前项目的 Playwright 1.62 满足版本要求。[官方 `Browser.bind`](https://playwright.dev/python/docs/api/class-browser#browser-bind) [官方 `BrowserType.connect`](https://playwright.dev/python/docs/api/class-browsertype#connect)
- 与 CDP attach 相比，Playwright 官方明确称 `connect_over_cdp()` 的 fidelity “significantly lower”，复杂功能应优先 `browser_type.connect()`。[官方 `connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#connect-over-cdp)
- 需要 Chromium Debugger/Network domain 时，连入后仍可通过 Playwright `new_cdp_session()` 使用 CDP，不需要把整个数据面降级成 `connect_over_cdp()`。

硬性接口语义：

- `open/ensure` 启动或复用 Holder；已有 profile owner 时不得二次启动。Playwright 官方明确说同一 User Data Directory 不能并发启动多个 browser。[官方 persistent-context 文档](https://playwright.dev/python/docs/api/class-browsertype#launch-persistent-context)
- `detach` 只清理该 MCP client 的 listeners/CDP sessions/本地索引并断开 client；不调用 owner context/page/browser 的 `close()`。
- `close(confirm=true)` 是唯一程序化关闭 owner 的入口。用户手动关浏览器窗口时 Holder 自然退出。
- MCP stdio 退出或重启不触发 Holder `close`。

### `cloakserve` 放在什么位置

`cloakserve` 是合理的 **可选 CDP compatibility adapter**，适合 Docker/Linux 服务、多框架连接、或客户端只认 CDP 的场景。它不适合直接替代当前 Windows 本地、用户需要看见窗口且长期保留 auth profile 的 Holder，原因是：

- 官方主要把它作为 Docker CDP server mode 发布；PyPI 的 console scripts 仅有 `cloakbrowser`，没有安装 `cloakserve`。[官方 `pyproject.toml`](https://github.com/CloakHQ/CloakBrowser/blob/main/pyproject.toml#L59-L65) [官方 Dockerfile](https://github.com/CloakHQ/CloakBrowser/blob/main/Dockerfile#L33-L39)
- 数据面是 CDP，Playwright 高阶功能的保真度低于原生 Playwright protocol。
- 它的 seed profile 默认是可回收临时数据，不等于本项目的持久 auth profile。
- CDP 拥有读页面、执行 JS 和控制浏览器的完整能力。官方也要求仅绑定 loopback，不得无认证暴露。[官方 CDP security warning](https://github.com/CloakHQ/CloakBrowser#cdp-server-mode)

## 最小迁移路径

1. 保留当前独立 Holder，它成为唯一 browser/profile owner。
2. Holder 启动 persistent context 后从 `context.browser` 取得 Browser，调用 `bind()` 并安全公布 endpoint。
3. `ReverseSession` 删除 MCP 内的 CloakBrowser launch path，改为 `browser_type.connect(endpoint)`。
4. 保留现有 network/console/script/debugger 逻辑；它们使用连入的 Playwright 对象，必要时创建 CDP session。
5. 将 `reverse_detach` 收紧为纯 client detach；将 owner shutdown 只放在明确 `cloak_debug_close(confirm=true)` 中。
6. 仅在需要第三方 CDP client 时增加可选 loopback CDP 出口，不默认开放。
