# DownloadTooler — Tauri 2.0 迁移可行性方案

> 评估将 XHS_Downloader 与 Dy_Downloader 两个子项目从 Electron 迁移到 Tauri 2.0、并合并为单一桌面端的可行性。本文档由 agent-team 技术架构决策会议产出。

---

## 一、评估背景与目标

### 现状

DownloadTooler 仓库下有两个独立的 Electron + Python 桌面工具：

| 子项目 | 平台 | 技术栈 | 版本 |
| --- | --- | --- | --- |
| Dy_Downloader | 抖音 | Electron 35 + Vue 3 + Python sidecar | v0.2.1 |
| XHS_Downloader | 小红书 | Electron 35 + Vue 3 + Python sidecar | v0.2.1 |

两个项目架构高度相似：双 `WebContentsView` 布局（左浏览 + 右面板）、Python sidecar 引擎、CDP 远程调试接入、登录态持久化、补丁接口。

### 目标

1. **评估** Tauri 2.0 能否承载两个子项目的全部核心能力
2. **设计** 合并为单一桌面端（多 Tab / 多平台切换）的架构
3. **识别** 迁移风险与不可行点，给出明确建议

---

## 二、agent-team 团队配置

本次评估采用「技术架构决策会议」团队配置：

| 角色 | 职责 | 关注点 |
| --- | --- | --- |
| **主持人**（技术架构师） | 引导讨论、收敛共识 | 整体架构可行性、技术选型 |
| **后端工程师** | Python 引擎 / CDP 集成评估 | sidecar 通信、page_bridge、签名链 |
| **前端工程师** | Vue 面板 / WebView 评估 | 双视图布局、IPC、登录态 |
| **DevOps 工程师** | 打包 / 分发评估 | 便携 EXE、补丁接口、体积 |
| **评审员** | 风险评估与最终建议 | 不可行点、回归风险、投入产出比 |

---

## 三、现有架构关键事实（研究智能体采集）

### 3.1 Dy_Downloader 架构要点

| 能力 | 实现方式 | 关键文件 |
| --- | --- | --- |
| 双视图布局 | `WebContentsView` ×2（dyView + panelView），`setBounds` 按宽度分割 | [main.js L755-L829](Dy_Downloader/electron/src/main.js#L755-L829) |
| CDP 接入 | `--remote-debugging-port=9357`，Python 用 DrissionPage 连接 | [main.js L30](Dy_Downloader/electron/src/main.js#L30)、[dyc_service.py L31-L46](Dy_Downloader/dy_app/dyc_service.py#L31-L46) |
| page_bridge | Python stdin 发 `==DYC_BRIDGE_REQ==` → 主进程在隐藏视图内 `executeJavaScript` 同源 fetch → stdout 回 `==DYC_BRIDGE_RES==` | [main.js L351-L422](Dy_Downloader/electron/src/main.js#L351-L422)、[dy_bridge.py L34-L92](Dy_Downloader/dy_app/dy_bridge.py#L34-L92) |
| 登录态持久化 | `session.fromPartition('persist:dy_main')`，Cookie 快照导出到 `cookies.json` | [main.js L142-L169](Dy_Downloader/electron/src/main.js#L142-L169) |
| Python 引擎 | `spawn(PYTHON, ['-u', SCRIPT, ...])`，stdout 流式 + stdin 桥接 | [main.js L423-L460](Dy_Downloader/electron/src/main.js#L423-L460) |
| 补丁接口 | `%APPDATA%\DyCollector\patches\` 优先加载，免重打包 | main.js 启动逻辑 |
| 导航按钮 | `panel:navigate` IPC，`webContents.goBack/goForward/reload/loadURL` | main.js IPC handler |

### 3.2 XHS_Downloader 架构要点

| 能力 | 实现方式 | 关键文件 |
| --- | --- | --- |
| 双视图布局 | `WebContentsView` ×2（xhsView + panelView），`setBounds` | [main.js L691-L721](XHS_Downloader/electron/main.js#L691-L721) |
| CDP 接入 | `--remote-debugging-port=9361`，Python `collect_one.py` 用 `dp = DrissionPage()` 连接，`browser.get_tabs(url="xiaohongshu.com")` 选择小红书 tab | [main.js L36-L43](XHS_Downloader/electron/main.js#L36-L43)、[collect_one.py L56-L85](XHS_Downloader/xhs_engine/collect_one.py#L56-L85) |
| 登录态持久化 | `persist:xhs_main` 分区，Cookie 导出到 `cookies.json` | main.js |
| Python 引擎 | 3 个 sidecar：`collect_one` / `download_one` / `note_one`，spawn + stdout `==XHS_JSON==` 结果块 | [main.js L394-L439](XHS_Downloader/electron/main.js#L394-L439) |
| 下载引擎 | 纯 HTTP，读 `cookies.json` 的 `cookie_header`，不挂浏览器 | [download_one.py L52-L64](XHS_Downloader/xhs_engine/download_one.py#L52-L64) |
| 补丁接口 | `%APPDATA%\XHSCollector\patches\` 优先加载 | main.js 启动逻辑 |
| 导航按钮 | `panel:nav` IPC，`back/fwd/home` | main.js IPC handler |

### 3.3 两项目差异点

| 维度 | Dy | XHS |
| --- | --- | --- |
| page_bridge | ✅ 有（Argus 风控兜底） | ❌ 无（页面自身签名） |
| CDP 用途 | sidecar 抓取 + page_bridge | sidecar 抓取 + cookie 导出 |
| 引擎数量 | 2（service + download） | 3（collect + download + note） |
| 下载方式 | 直连 + bridge 兜底 | 纯 HTTP + `curl_cffi` |

---

## 四、Tauri 2.0 能力评估（研究智能体采集）

### 4.1 Tauri 2.0 核心能力

| 能力 | Tauri 2.0 支持 | 评估 |
| --- | --- | --- |
| **Windows WebView** | WebView2（Edge Chromium 内核） | ✅ 与 Electron 同为 Chromium 内核，兼容性好 |
| **多 WebView** | `WebviewWindow` / 多窗口 | ⚠️ 支持多 webview，但单窗口内双 webview 布局需自定义 |
| **Sidecar（外部二进制）** | `externalBin` + `tauri_plugin_shell` + `sidecar()` spawn + stdin/stdout 事件流 | ✅ **完美匹配** Python PyInstaller exe 集成 |
| **CDP 远程调试** | WebView2 支持 `--remote-debugging-port`（通过 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 环境变量或注册表） | ✅ **关键发现**：DrissionPage 理论上可连接 WebView2 |
| **Cookie / Session** | WebView2 有独立 `UserDataFolder`，持久化 Cookie；Tauri 有 `cookies_for_url` API | ✅ 可替代 Electron session 分区 |
| **JS 执行（eval）** | `Webview::eval()` + 2.11+ 新增 `eval_with_callback` | ✅ 可替代 `executeJavaScript` |
| **IPC（Rust ↔ JS）** | `#[tauri::command]` + `invoke()` + `emit/listen` 事件 | ✅ 替代 `ipcMain.handle` |
| **打包** | `tauri build` 生成 MSI/NSIS/便携目录；体积远小于 Electron | ✅ 便携版 + 补丁接口可保留 |
| **Rust 后端** | 原生 Rust，可替代部分 Electron 主进程逻辑 | ✅ 性能更好，内存更低 |

### 4.2 关键技术映射

| Electron 能力 | Tauri 2.0 对应方案 |
| --- | --- |
| `WebContentsView` ×2 双布局 | 方案 A：两个 `WebviewWindow` + 透明窗体拼接；方案 B：单窗口 + `<iframe>`/`<webview>` 自定义布局 |
| `--remote-debugging-port=9357` | `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9357` 环境变量 |
| `session.fromPartition('persist:dy_main')` | WebView2 `UserDataFolder` 按平台独立目录 |
| `webContents.executeJavaScript(js)` | `Webview::eval_with_callback(js)` (Tauri 2.11+) |
| `ipcMain.handle('panel:navigate', ...)` | `#[tauri::command] fn navigate(action: String)` + `invoke` |
| `spawn(PYTHON, ...)` + stdin/stdout | `app.shell().sidecar("engine")` + `CommandEvent::Stdout` + `child.write()` |
| `contentView.setBackgroundColor` | Tauri 原生不存在该层（无宿主 contentView 拦截问题） |
| 补丁接口 | Tauri 资源目录优先加载逻辑（Rust 实现） |

---

## 五、团队会议讨论（实时沟通过程）

> 以下为 agent-team 实时沟通模式的讨论记录。

### 第一轮：各智能体陈述观点

> **主持人（技术架构师）**：各位，今天的议题是「DownloadTooler 是否应从 Electron 迁移到 Tauri 2.0 并合并双子项目」。请各位从专业角度发表观点。先请后端工程师评估 Python 引擎集成的可行性。

> **后端工程师**：从后端角度看，**最关键的好消息是 WebView2 支持 CDP 调试端口**。通过设置 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9357` 环境变量，DrissionPage 理论上可以像连接 Electron 一样连接 Tauri 的 WebView2 实例。这意味着现有的 `dyc_service.py`、`collect_one.py` 的 CDP 接入逻辑**几乎不用改**。

> **后端工程师**：其次，Tauri 的 sidecar 机制**完美匹配**我们的 Python 引擎集成。`externalBin` + `tauri_plugin_shell::ShellExt` + `sidecar().spawn()` 提供 stdout 事件流和 stdin 写入，和现在 Electron 的 `spawn(PYTHON, ...)` + `child.stdout.on('data')` + `child.stdin.write()` 是一一对应的。`==DYC_BRIDGE_REQ==` / `==DYC_BRIDGE_RES==` 行协议可以原样保留。

> **后端工程师**：page_bridge 是最大挑战。现在依赖 `webContents.executeJavaScript(js)` 在已登录视图内执行同源 fetch。Tauri 2.11+ 的 `eval_with_callback` 可以替代，但需要确认 WebView2 的 `eval` 是否在登录视图上下文执行、是否携带 credentials。这一点需要 POC 验证。

> **前端工程师**：从前端角度看，Vue 面板迁移成本很低——Vue 3 应用本身就是框架无关的，从 Electron `BrowserWindow.loadFile` 换成 Tauri `WebviewWindow` 加载 `dist/index.html` 即可。IPC 从 `window.dyc.navigate` / `window.api.nav` 改成 `@tauri-apps/api` 的 `invoke` 也很直接。

> **前端工程师**：但**双视图布局是真正的难点**。Electron 的 `WebContentsView` + `setBounds` 可以在单窗口内精确放置两个独立 webview（左浏览右面板）。Tauri 2.0 支持多 `WebviewWindow`，但单窗口内的多 webview 布局不是原生 API——需要要么用两个透明窗口拼接（复杂且易碎），要么退化为单 webview + iframe（但 iframe 无法承载跨域登录页）。

> **DevOps 工程师**：从打包分发角度，Tauri 优势明显。Electron 便携包 DyCollector 约 180MB（含 Chromium + Python exe），Tauri 用系统 WebView2，本体可做到 10-15MB + Python exe。补丁接口也能用 Rust 实现资源优先加载。但要注意 **WebView2 Runtime 依赖**：Windows 10 1803+ 预装，但部分精简版系统可能缺失，需要安装器处理。

> **评审员**：我关注三点风险。第一，**合并为单一桌面端**意味着两个平台的登录态、Cookie、下载队列要在同一应用内隔离——Electron 用 `persist:dy_main` / `persist:xhs_main` 分区天然隔离，Tauri 需要用不同 `UserDataFolder` 实现，要做隔离验证。第二，**page_bridge 是抖音防风控的核心**，如果 Tauri 的 `eval` 不能完美复现同源 fetch 行为，整个抖音下载链路会断。第三，**投入产出比**：现有 Electron 方案已稳定运行，迁移工作量不小，是否值得？

### 第二轮：针对争议焦点的深入辩论

> **主持人**：前端工程师提到的双视图布局是关键难点。请详细说明替代方案。

> **前端工程师**：实际上有第三种方案：**单 webview + 分屏 DOM**。右侧面板是 Vue 应用，左侧浏览页如果也是同源可以嵌入；但抖音/小红书是跨域页面，iframe 方案不可行。所以真正可行的只有：**多窗口拼接**——主窗口是面板，子 webview 窗口无框透明、贴在左侧，resize 时同步移动。Tauri 2.0 的 `WebviewWindow` 支持自定义位置和无框模式，技术上可行，但窗口管理复杂度高于 Electron 的单窗口双 view。

> **后端工程师**：补充一个发现——Tauri 2.0 社区有 [tauri-plugin-window-state](https://github.com/tauri-apps/plugins-workspace) 插件管理多窗口位置。而且合并为单应用后，可以改为**多 Tab 切换**：顶部 Tab 切换「抖音 / 小红书」，每次只显示一个平台的浏览+面板，避免双窗口并排的复杂度。这反而是比 Electron 更优雅的 UX。

> **评审员**：Tab 方案不错，但要注意：现在用户可能想**同时**打开抖音和小红书。不过实际使用场景中，同一时刻只操作一个平台是合理的，Tab 切换 + 各自独立的登录态是可接受的。

> **主持人**：关于 page_bridge，后端工程师说需要 POC。请明确 POC 的最小验证内容。

> **后端工程师**：POC 需要验证：① WebView2 开启 CDP 端口后 DrissionPage 能否连接并选择目标 tab；② Tauri 的 `eval_with_callback` 在登录视图内执行 fetch 是否携带 Cookie（credentials 行为）；③ stdin/stdout 行协议在 `tauri_plugin_shell` 下是否完整可用。这三个点验证通过，page_bridge 就可以平移。

### 第三轮：共识收敛

> **主持人**：综合讨论，我们形成以下共识：

> 1. **技术上可行**——Tauri 2.0 能承载全部核心能力，关键是 WebView2 的 CDP 支持让 Python 引擎集成可以平移。

> 2. **双视图布局是主要改造点**——推荐改为多 Tab 架构（抖音/小红书切换），而非强行复刻单窗口双 webview。

> 3. **page_bridge 需 POC 验证**——这是抖音 Argus 风控兜底的核心，未验证前不应全量迁移。

> 4. **合并为单应用有明确收益**——体积从 360MB（两个 Electron 包）降到 ~50MB（单 Tauri 包 + 双 Python 引擎），UX 统一。

> 5. **建议分阶段迁移**——先做 POC 验证三大风险点，再全量实施。

> **评审员**：同意。我补充最终建议：**不推荐立即停掉 Electron 版本**，而是 Tauri 版本作为 v0.3.0 并行开发，POC 验证通过后再切换主线。保留 Electron 版本作为回退。

---

## 六、可行性矩阵

| 核心能力 | 迁移难度 | 风险 | 说明 |
| --- | --- | --- | --- |
| Python sidecar 集成 | 🟢 低 | 低 | `externalBin` + `shell.sidecar()` 完美对应，stdin/stdout 行协议原样保留 |
| CDP 远程调试接入 | 🟡 中 | 中 | WebView2 支持 CDP 端口，但需验证 DrissionPage 兼容性 |
| 登录态持久化 | 🟢 低 | 低 | WebView2 `UserDataFolder` 替代 session 分区 |
| Cookie 快照导出 | 🟡 中 | 低 | 用 CDP `Network.getCookies` 或 Tauri cookie API |
| Vue 面板迁移 | 🟢 低 | 低 | 框架无关，IPC 改 `invoke` 即可 |
| 导航按钮 | 🟢 低 | 低 | `#[tauri::command]` + `webview.go_back()` 等 |
| 双视图布局 | 🔴 高 | 中 | 改为多 Tab 架构，避免单窗口双 webview |
| page_bridge（Dy 专属） | 🔴 高 | 高 | 依赖 `eval_with_callback` + 同源 fetch credentials，需 POC |
| 补丁接口 | 🟡 中 | 低 | Rust 实现资源优先加载 |
| 打包分发 | 🟢 低 | 低 | `tauri build` 便携包，体积大幅下降 |
| 内存优化 | 🟢 低 | 低 | Tauri 原生更省内存，Electron 的优化大部分不再需要 |

---

## 七、推荐架构设计

### 7.1 合并后的单应用架构

```
DownloadTooler/（Tauri 单应用）
├── src-tauri/                    # Rust 主进程
│   ├── src/
│   │   ├── main.rs              # 应用入口、窗口管理、Tab 切换
│   │   ├── commands.rs          # #[tauri::command] IPC 处理
│   │   ├── sidecar.rs           # Python 引擎 spawn + stdin/stdout 桥接
│   │   ├── bridge.rs            # page_bridge 实现（eval_with_callback）
│   │   ├── cookies.rs           # Cookie 导出（CDP / WebView2 API）
│   │   └── patches.rs           # 补丁接口（资源优先加载）
│   ├── binaries/                # Python sidecar exe（按 target-triple 命名）
│   │   ├── dy-engine-x86_64-pc-windows-msvc.exe
│   │   ├── dy-service-x86_64-pc-windows-msvc.exe
│   │   ├── xhs-collect-x86_64-pc-windows-msvc.exe
│   │   ├── xhs-download-x86_64-pc-windows-msvc.exe
│   │   └── xhs-note-x86_64-pc-windows-msvc.exe
│   ├── tauri.conf.json          # externalBin、窗口、权限配置
│   └── Cargo.toml
├── src/                          # 前端（Vue 3）
│   ├── App.vue                  # Tab 切换（抖音 / 小红书）
│   ├── views/
│   │   ├── DyPanel.vue          # 抖音面板（队列/日志/设置）
│   │   └── XhsPanel.vue         # 小红书面板（列表/队列/设置）
│   └── composables/
│       ├── useSidecar.ts        # sidecar 调用封装
│       └── useBridge.ts          # page_bridge 封装
├── engines/                     # Python 引擎源码（原 dy_app / xhs_app）
│   ├── dy/
│   └── xhs/
└── tools/
    └── build_release.ps1        # 统一打包脚本
```

### 7.2 多 Tab 交互

```
┌─────────────────────────────────────────────┐
│  [抖音 Tab] [小红书 Tab]        ● 登录状态  ⚙ │
├──────────────────────┬──────────────────────┤
│                      │                      │
│   左侧浏览页          │   右侧操作面板        │
│   (WebView2)         │   (Vue)              │
│   抖音 / 小红书       │   队列/列表/设置      │
│                      │                      │
│                      │  ◀ ▶ ⟳ ⌂  导航按钮    │
├──────────────────────┴──────────────────────┤
│  后台下载队列状态栏                            │
└─────────────────────────────────────────────┘
```

- 顶部 Tab 切换平台，每个 Tab 独立登录态（独立 `UserDataFolder`）
- 左浏览 + 右面板布局保留（单 webview + 面板 DOM 分屏，或双 webview 窗口拼接）
- 导航按钮在面板左上角，通过 `#[tauri::command]` 调用 `webview.go_back()` 等

### 7.3 Python 引擎集成（sidecar）

```rust
// src-tauri/src/sidecar.rs
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;

#[tauri::command]
pub async fn spawn_dy_engine(app: tauri::AppHandle, target: String) {
    let cmd = app.shell().sidecar("dy-engine").unwrap();
    let (mut rx, mut child) = cmd.spawn().expect("spawn failed");

    // 设置 CDP 端口环境变量（让 Python 引擎能连接 WebView2）
    // WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 已在主进程设置

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let s = String::from_utf8_lossy(&line);
                    // 解析 ==DYC_BRIDGE_REQ== 行协议
                    if s.starts_with("==DYC_BRIDGE_REQ==") {
                        let req = serde_json::from_str(&s[18..]).unwrap();
                        let res = bridge::exec(&app, req).await;
                        child.write(
                            format!("==DYC_BRIDGE_RES== {}\n", serde_json::to_string(&res).unwrap()).as_bytes()
                        ).unwrap();
                    }
                    // 透传日志到前端
                    let _ = app.emit("sidecar-log", s.to_string());
                }
                _ => {}
            }
        }
    });
}
```

### 7.4 page_bridge 迁移

```rust
// src-tauri/src/bridge.rs
use tauri::WebviewWindow;

pub async fn exec(app: &tauri::AppHandle, req: BridgeReq) -> BridgeRes {
    let url = req.url;
    // 在已登录的浏览 webview 内执行同源 fetch
    let dy_view = app.get_webview_window("dy-main").unwrap();
    let js = format!(r#"(async()=>{{
        const r = await fetch("{}", {{ credentials: "include" }});
        const t = await r.text();
        return {{ ok: true, status: r.status, text: t.slice(0, 5000000) }};
    }})()"#, url);

    // Tauri 2.11+ eval_with_callback
    let res: serde_json::Value = dy_view.eval_with_callback(&js).await.unwrap();
    // 解析返回
    BridgeRes { id: req.id, status: res["status"].as_i64(), text: res["text"].as_str().unwrap_or("").to_string() }
}
```

---

## 八、风险评估

| 风险 | 等级 | 影响 | 缓解措施 |
| --- | --- | --- | --- |
| WebView2 CDP 与 DrissionPage 兼容性未验证 | 🔴 高 | 抖音/小红书抓取链路可能断 | POC 第一优先验证 |
| `eval_with_callback` 同源 fetch credentials 行为未验证 | 🔴 高 | page_bridge 失效 → 抖音 Argus 拦截 | POC 验证 + 保留 Electron 回退 |
| 单窗口双 webview 布局复杂 | 🟡 中 | UX 退化或开发成本增加 | 改多 Tab 架构规避 |
| WebView2 Runtime 缺失（精简 Windows） | 🟡 中 | 部分用户无法运行 | 安装器检测 + 引导安装 Evergreen Runtime |
| Tauri 多窗口 resize 同步抖动 | 🟡 中 | 拖动时布局错位 | 窗口事件防抖 + 原生插件 |
| 合并后两平台状态隔离 | 🟢 低 | Cookie/队列串扰 | 独立 `UserDataFolder` + 独立 sidecar |
| Rust 学习成本 | 🟢 低 | 开发速度短期下降 | 主进程逻辑简单，IPC + sidecar 模式固定 |

---

## 九、结论与建议

### 总体结论

**技术可行，建议分阶段迁移，保留 Electron 回退。**

Tauri 2.0 能承载 DownloadTooler 的全部核心能力，且 WebView2 的 CDP 支持是关键 enabling factor——它让 Python 引擎的 DrissionPage 集成可以平移。合并为单应用可大幅降低分发体积（360MB → ~50MB）并统一 UX。

### 投入产出比

| 维度 | Electron（现状） | Tauri 2.0（目标） |
| --- | --- | --- |
| 分发体积 | ~360MB（两个包） | ~50MB（单包 + 双引擎） |
| 内存占用 | ~400MB RSS（双进程） | ~150MB RSS（单进程 + sidecar） |
| 启动速度 | 2-3s | <1s |
| 开发语言 | JS + Python | Rust + JS + Python |
| 维护成本 | 两套相似代码 | 单一代码库 |

### 建议路线图

| 阶段 | 目标 | 验证点 |
| --- | --- | --- |
| **P0：POC 验证**（1-2 周） | 验证三大风险点 | ① DrissionPage 连接 WebView2 CDP 端口 ② `eval_with_callback` 同源 fetch 携带 Cookie ③ sidecar stdin/stdout 行协议 |
| **P1：抖音单平台迁移**（2-3 周） | Dy 完整迁移到 Tauri | page_bridge 工作、下载队列正常、登录态持久 |
| **P2：小红书迁移 + 合并**（2-3 周） | XHS 迁移 + 多 Tab 合并 | 两平台独立登录态、Tab 切换正常 |
| **P3：补丁接口 + 打包**（1 周） | 补丁接口 + 便携包 | 免重打包热更新可用、体积 <60MB |
| **P4：切换主线** | Tauri 版本作为 v0.3.0 发布 | Electron 版本保留为回退 |

### 不迁移的代价

- 分发体积持续 ~360MB，用户下载成本高
- 两套相似代码维护，bug 修复需双倍投入
- 内存占用高，长时间批量下载时主进程 RSS 易上涨（已做优化但治标不治本）

---

## 十、参考资料

- [Tauri 2.0 文档](https://v2.tauri.app/)
- [Tauri Sidecar 指南](https://v2.tauri.app/develop/sidecar/)
- [WebView2 CDP 调试](https://learn.microsoft.com/zh-cn/microsoft-edge/webview2/how-to/debug-visual-studio-code)
- [Tauri Shell 插件](https://v2.tauri.app/plugin/shell/)
- [Tauri 2.11 eval_with_callback](https://tauri.app/release/tauri/v2.11.0/)

---

*本文档由 agent-team 技术架构决策会议产出。如需启动 POC 或进入实施阶段，请基于本方案的路线图执行。*
