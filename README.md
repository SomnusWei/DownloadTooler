# DownloadTooler

个人自用的 Windows 桌面下载工具（**Tauri 2.0** 单一应用）：内置**原生站点窗口**浏览抖音 / 小红书，在**窗口内按快捷键**即可下载**当前正在看的作品 / 笔记**，用于备份自己收藏或已获授权的公开内容。

> v0.3.0 起桌面端由 Electron 迁移到 Tauri 2.0，**不再提供 Vue 面板**：平台窗口就是完整站点页面，交互只剩一个下载快捷键。

## 功能一览

| 能力 | 说明 |
| --- | --- |
| 单应用启动 | 启动只有「配置中心」一个窗口（4 标签：启动 / 登录态 / 设置 / 日志） |
| 按需拉起平台窗口 | 点击按钮或按快捷键打开 `dy_main` / `xhs_main`；已打开则聚焦，不重复创建 |
| **窗口内下载快捷键** | 默认 `Ctrl+D`：下载**当前显示**的作品 / 笔记；仅在对应窗口聚焦时生效，不影响其他应用的 Ctrl+D |
| 窗口启动快捷键 | 默认 `Alt+1` 抖音 / `Alt+2` 小红书（系统级，托盘状态下也生效） |
| 抖音下载 | **视频**（无水印，清晰度可选 `最高 / 1080P / 720P / 540P`）与 **图集/图文**（无水印原图按序落盘） |
| 小红书下载 | 笔记 **原图 / 视频**（固定最高清晰度） |
| 状态提示 | 平台窗口**右下角 toast**：已加入下载 → 开始下载 → 下载进度 → 下载完成/失败；渐入渐出、向上堆叠、最多 5 条 |
| 下载日志 | 配置中心「日志」标签实时查看引擎输出 |
| 登录态 | Cookie 本地持久化，重启免登录；**双轨检测**（Cookie 存在性 + CDP 真实登录态校验） |
| 系统托盘 | 关闭窗口最小化到托盘；托盘右键「显示主窗口 / 退出」 |
| 补丁接口 | 引擎可**免重打包热更新**（见下） |

## 架构

```
┌─ Tauri 2.0 单一应用 ────────────────────────────────────────────┐
│  panel_main（配置中心，Vue 4 标签）                              │
│  dy_main / xhs_main（WebView2 原生站点窗口，各自独立 CDP 端口）  │
│      · dy_main   → CDP 9357                                      │
│      · xhs_main  → CDP 9361                                      │
└───────────────┬─────────────────────────────────────────────────┘
                │ shell().command() + DT_TASK 环境变量
                ↓
    Python 引擎（多入口合并 onedir，PyInstaller 打包）
      DT_TASK=dy-download   → 抖音：签名取流 → 视频/图集落盘
      DT_TASK=xhs-note      → 小红书：CDP 挂接解析当前笔记
      DT_TASK=xhs-download  → 小红书：纯 HTTP + cookies.json 下载
```

关键机制：

- **page_bridge（抖音 Argus 兜底）**：抖音对详情接口启用 Argus 风控，非页面请求即使签名与 Cookie 正确也会 `403`。引擎直连失败时通过 stdout/stdin 行协议（`==DYC_BRIDGE_REQ==` / `==DYC_BRIDGE_RES==`）把请求交给 Tauri 主进程，在**已登录的 dy_main 内以同源 fetch** 执行。
- **抖音当前作品识别**：前台多用 **modal 弹窗**展示作品（URL 只有 `modal_id`），因此不能只看 URL —— 采用**视口中心 DOM 探测**（元素上溯 14 层找作品链接或 `data-*` 里的长数字 id），URL 仅作兜底。
- **小红书必须带 `xsec_token`**：解析笔记 SSR 依赖当前页 URL 的 `xsec_token`，故传参保留完整查询串。
- **窗口内快捷键的实现**：只依赖聚焦状态 —— 平台窗口获得焦点时注册 `Ctrl+D`，失焦即注销（Tauri v2 默认禁止远程站点页面调用 IPC，故不走页面内注入）。
- **CDP 端口隔离**：dy_main / xhs_main 各自独立 `UserDataFolder` 与 `--remote-debugging-port`，两平台可**并发下载**互不干扰。

## 目录结构

```
DownloadTooler/
├── tauri-app/                     # ★ 桌面端（Tauri 2.0）
│   ├── src/App.vue                # 配置中心 UI（启动/登录态/设置/日志）
│   ├── src-tauri/src/
│   │   ├── lib.rs                 # 入口：托盘 / 快捷键（常驻 + 聚焦域内） / sidecar 注册
│   │   ├── windows.rs             # 单应用 + 按需窗口（CDP 9357 / 9361）
│   │   ├── cookies.rs             # CDP WebSocket + 双轨登录检测
│   │   ├── sidecar.rs             # 引擎 spawn / 行协议 / 日志透传 / 生命周期
│   │   ├── bridge.rs              # page_bridge：dy_main 内同源 fetch
│   │   ├── download.rs            # 下载编排：识别当前作品 → 起引擎 → toast/日志
│   │   ├── toast.rs               # 平台窗口右下角状态提示
│   │   ├── patches.rs             # 补丁接口（引擎热更新）
│   │   └── settings.rs / paths.rs
│   └── tools/
│       ├── engine_launcher.py     # 多入口启动器（按 DT_TASK 分发）
│       ├── build_sidecars.ps1     # PyInstaller onedir 打包合并引擎
│       └── build_release.ps1      # 一键 build + 组装便携目录 + zip
├── Dy_Downloader/                 # 抖音侧 Python 引擎与签名
│   └── dy_app/                    # dyc_download / dy_fetch / dy_bridge / dyc_sign
├── XHS_Downloader/                # 小红书侧 Python 引擎
│   ├── xhs_app/                   # 解析 / 详情 / 下载 / 队列
│   └── xhs_engine/                # 引擎入口（note_one / download_one）
├── 迁移进度.md                    # 迁移路线与进度（P0–P4）
└── Tauri 2.0方案.md               # 迁移方案
```

## 构建与运行

### 前置

- Node.js ≥ 18、Rust（MSVC 工具链）
- Python 环境：`Dy_Downloader/.venv` 与 `XHS_Downloader/.venv`（各自 `pip install -r requirements.txt`）
- WebView2 Runtime（Win11 内置；安装包会检测并引导安装）

### 开发

```powershell
# 1) 打包 Python 引擎（产出 src-tauri/engines/dt-engine/）
cd tauri-app
powershell -ExecutionPolicy Bypass -File tools\build_sidecars.ps1

# 2) 启动开发模式
npm install
npm run tauri dev
```

### 发布

```powershell
cd tauri-app
powershell -ExecutionPolicy Bypass -File tools\build_release.ps1 -Version 0.3.0
```

产物：

| 形态 | 路径 | 体积 |
| --- | --- | --- |
| MSI 安装包 | `src-tauri/target/release/bundle/msi/` | ~23 MB |
| NSIS 安装包 | `src-tauri/target/release/bundle/nsis/` | ~18 MB |
| 便携包 | `build/release/DownloadTooler-v<版本>-portable.zip` | ~23 MB |

便携版解压后运行 `DownloadTooler.exe`，需与 `engines/` 同目录。

## 数据与本地文件

| 内容 | 位置 |
| --- | --- |
| 配置（下载目录 / 快捷键 / 清晰度） | `%LOCALAPPDATA%\DownloadTooler\panel_data\settings.json` |
| 平台窗口登录态 | `%LOCALAPPDATA%\DownloadTooler\dy_data`、`...\xhs_data` |
| Cookie 快照（供引擎取流） | `%LOCALAPPDATA%\DyCollector\cookies.json`、`%LOCALAPPDATA%\XHSCollector\cookies.json` |
| 引擎补丁 | `%APPDATA%\DownloadTooler\patches\engine\` |
| 下载结果 | 你在「设置」里选择的目录（默认系统「下载」） |

下载落盘结构：抖音 `下载目录/作者/标题/`（视频 `视频.mp4`，图集 `01.jpg…`）；小红书 `下载目录/作者/标题_<笔记ID>/`。

## 补丁接口（免重打包热更新）

把重建的引擎放到补丁目录即可，**下次调用引擎自动生效**（按以下顺序取第一个命中的）：

```
%APPDATA%\DownloadTooler\patches\engine\
├── dt-engine-x86_64-pc-windows-msvc.exe   # 独立 exe
├── dt-engine.exe                          # 独立 exe
└── dt-engine\dt-engine.exe                # 整目录替换（推荐）
```

> 引擎已合并为单文件多入口（`dt-engine`，按 `DT_TASK` 分发），因此补丁是**整目录/整引擎替换**。

## 合规声明

本项目仅供**个人学习、研究及已获授权内容的备份**使用；请勿用于商业用途或侵犯他人权益，请控制请求频率并遵守平台规则。使用者应自行承担使用本项目产生的全部责任。
