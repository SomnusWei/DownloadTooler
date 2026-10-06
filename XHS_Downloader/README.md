# XHS_Downloader（小红书引擎侧）

小红书侧的 **Python 引擎实现**，被 [tauri-app](../tauri-app) 打包进合并引擎 `dt-engine` 调用。
桌面端（窗口、快捷键、日志、托盘等）见根目录 [README.md](../README.md)。

## 引擎入口

| 任务 | 入口 | 参数 | 说明 |
| --- | --- | --- | --- |
| `xhs-note` | `xhs_engine/note_one.py` | `--href <笔记链接>`（留空 = 取当前页） | 挂接 CDP **9361** 解析当前笔记，输出 `{ok, meta, note}` |
| `xhs-download` | `xhs_engine/download_one.py` | `--note-json <file> --dir <下载根目录>` | 纯 HTTP + 本地 `cookies.json`，输出 `{ok, state, msg}` |

调用方式（由 Tauri 主进程注入 `DT_TASK` 与 `XHS_ROOT`）：

```powershell
# ① 解析当前笔记
$env:DT_TASK = "xhs-note"; $env:XHS_ROOT = "$env:LOCALAPPDATA\XHSCollector"
dt-engine.exe --href "https://www.xiaohongshu.com/explore/<note_id>?xsec_token=..."

# ② 下载（note-json 为 {meta, note} 结构）
$env:DT_TASK = "xhs-download"
dt-engine.exe --note-json %TEMP%\note.json --dir D:\Downloaded
```

- **必须保留完整 URL（含 `xsec_token`）**：解析笔记 SSR 依赖它，缺 token 会失败
- 落盘：`<dir>/作者/标题_<笔记ID>/`，图片 `01.jpg…`、视频 `视频.mp4`（**含 noteId 后缀，与抖音结构不同**）
- 无百分比进度：引擎按文件输出 `[<note_id>] 图 3` / `[<note_id>] 视频`，调用方据此做**文件级**进度展示
- 失败分支只打印**裸 JSON**（不带 `==XHS_JSON==` 标记），调用方需兜底解析

## 技术要点

- **Cookie 入口是 `XHS_ROOT`**：引擎读取 `%XHS_ROOT%\cookies.json`，下载依赖其中的 `cookie_header`（由 Tauri 侧导出）
- **采集**：页面自身携带 `x-s` 签名发请求，只监听/收割响应并合并 SSR 首屏；纯 API 翻页不可行（签名逐请求变化）
- **详情与下载**：`curl_cffi` 携带 Cookie 快照直取笔记页 HTML，解析 SSR 状态取原图/视频地址，媒体直连 CDN —— 全程后台 HTTP，不占用浏览器

## 依赖与运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

- 引擎所需：`DrissionPage`、`curl_cffi`、`PyYAML`、`httpx`、`requests`
- `PySide6` 仅 `main.py` / `embed.py` / `ui.py` / `single.py` 等**独立 Qt GUI** 使用；Tauri 版**不打包**这些模块

## 目录结构

```
XHS_Downloader/
├── xhs_engine/
│   ├── note_one.py            # 单篇笔记解析（挂 CDP 9361）
│   ├── download_one.py        # 单篇下载（纯 HTTP + cookies.json）
│   └── collect_one.py         # 作者主页列表采集（旧 Electron 版使用，Tauri 版未接入）
├── xhs_app/
│   ├── collector.py           # 列表采集（响应监听 + SSR 兜底）
│   ├── detail.py              # 笔记详情（SSR 解析原图/视频）
│   ├── download.py            # 命名 / 跳过 / 续传等下载工具
│   ├── queue.py               # 任务模型
│   ├── resolver.py / service.py / models.py / config.py
│   └── embed.py / ui.py / single.py   # 独立 Qt GUI 相关（未随 Tauri 版分发）
├── main.py                    # 独立 Qt GUI 入口（未随 Tauri 版分发）
├── requirements.txt
└── tools/make_icon.py
```

## 数据与本地文件

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `cookies.json` | `%LOCALAPPDATA%\XHSCollector\` | 浏览器 Cookie 快照（含 `cookie_header`），由 Tauri 侧导出 |
| 下载结果 | Tauri 配置中心里选择的下载目录 | `<作者>/<标题>_<笔记ID>/` |

## 合规声明

本项目仅供**个人学习、研究及已获授权内容的备份**使用；请勿用于商业用途或侵犯他人权益，请控制请求频率并遵守平台规则。使用者应自行承担使用本项目产生的全部责任。
