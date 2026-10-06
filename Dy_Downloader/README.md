# Dy_Downloader（抖音引擎侧）

抖音侧的 **Python 引擎与签名实现**，被 [tauri-app](../tauri-app) 打包进合并引擎 `dt-engine` 调用。
桌面端（窗口、快捷键、日志、托盘等）见根目录 [README.md](../README.md)。

## 引擎入口

| 任务 | 入口 | 参数 |
| --- | --- | --- |
| `dy-download` | `dy_app/dyc_download.py` | `--url <作品链接> --dir <下载根目录> [--quality 1080] [--cookies-file <cookies.json>]` |

调用方式（由 Tauri 主进程注入 `DT_TASK` 与 `DYC_BRIDGE=1`）：

```powershell
$env:DT_TASK = "dy-download"; $env:DYC_BRIDGE = "1"
dt-engine.exe --url https://www.douyin.com/video/7xxxxxxxxxxxxxxxxxx --dir D:\Downloaded `
              --cookies-file %LOCALAPPDATA%\DyCollector\cookies.json
```

输出：过程日志走 stdout；结束时打印 `==DYC_JSON==` + 结果 JSON（`{ok, id, type, title, author, folder, files, total_bytes}`）。

- **视频**：按 `--quality` 选流；缺省/空 = 分辨率最高档（按「分辨率优先、码率次之」排序取首个），指定档位则精确匹配，无匹配回退最高
- **图集/图文**：识别 `images` 作品，取 `url_list` 无水印原图按序下载
- 落盘：`<dir>/作者/标题/`，视频 `视频.mp4`，图集 `01.jpg`、`02.webp`…
- 失败分支只打印**裸 JSON**（不带 `==DYC_JSON==` 标记），调用方需兜底解析

## 关键机制：为什么需要 page_bridge

抖音自 2026-09 起对 `aweme/v1/web/aweme/detail/` 等接口启用 **Argus 风控**：即便 Cookie 与本地签名都正确，非页面请求也会被 `403 Blocked by ArgusSecurityPlugin` 拦掉。因此采用两层取流：

1. **直连优先**：本地生成 `a_bogus`（SM3+RC4，`dyc_sign/abogus.py`）与 `X-Bogus` 签名，携带 Cookie 直连；
2. **page_bridge 兜底**：直连失败时通过 stdout/stdin 行协议把请求交给宿主进程，在**已登录的抖音页面内以同源 fetch** 执行，再把响应回传引擎：

```
引擎   → stdout : ==DYC_BRIDGE_REQ== {"id":1,"url":"...","method":"GET"}
宿主   → stdin  : ==DYC_BRIDGE_RES== {"id":1,"http_status":200,"text":"..."}
```

要点：

- 页面通道必须使用**未签名 URL**（叠加本地签名会被判 `Sign Invalid`）
- `uifid` / `msToken` 必须用**实时 Cookie** 覆盖引擎快照值（快照轮换后不一致会返回 404/HTML）
- `msToken` 优先向 `mssdk.bytedance.com` 申请真实 token，失败回落随机 token（`dyc_sign/ms_token.py`）
- 打包 exe 需强制 UTF-8 stdio（否则大 JSON 应答解码失败，见 `dyc_download.py::_force_utf8_stdio`）

## 依赖与运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

- 引擎所需：`DrissionPage`、`curl_cffi`、`gmssl`
- `PySide6` 仅 `main.py` / `host.py` / `embed.py` / `ui.py` 等**独立 Qt GUI** 使用；Tauri 版**不打包**这些模块（已在引擎构建中 `--exclude-module PySide6`）

## 目录结构

```
Dy_Downloader/
├── dy_app/
│   ├── dyc_download.py        # 引擎入口：取流 → 落盘（视频/图集）
│   ├── dy_fetch.py            # 详情接口：参数构造、签名、page_bridge 兜底
│   ├── dy_bridge.py           # 引擎侧 page_bridge 客户端（stdin/stdout 协议）
│   ├── dyc_sign/              # abogus / xbogus / ms_token（+ F2 配置快照）
│   ├── dyc_service.py         # 主页/收藏清单采集（旧 Electron 版使用，Tauri 版未接入）
│   ├── img_engine.py          # 图集「页面驱动」取图（备选路径，Tauri 版未接入）
│   └── cookies.py / config.py / embed.py / host.py / ui.py / single.py
├── main.py                    # 独立 Qt GUI 入口（未随 Tauri 版分发）
├── requirements.txt
└── tools/                     # 图标与探测脚本
```

## 数据与本地文件

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `cookies.json` | `%LOCALAPPDATA%\DyCollector\` | 浏览器 Cookie 快照，由 Tauri 侧导出，供引擎取流 |
| 下载结果 | Tauri 配置中心里选择的下载目录 | `<作者>/<标题>/` |

## 合规声明

本项目仅供**个人学习、研究及已获授权内容的备份**使用；请勿用于商业用途或侵犯他人权益，请控制请求频率并遵守平台规则。使用者应自行承担使用本项目产生的全部责任。
