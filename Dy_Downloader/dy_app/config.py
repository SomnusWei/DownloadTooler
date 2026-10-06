# -*- coding: utf-8 -*-
"""Dy_Downloader 全局路径与常量"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：资源在 _MEIPASS；登录态/缓存/配置放 %LOCALAPPDATA%
    APP_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    ROOT = Path(sys.executable).resolve().parent
    ICON_PATH = APP_DIR / "logo.ico"
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "DyCollector"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    APP_DIR = Path(__file__).resolve().parent          # dy_app/
    ROOT = APP_DIR.parent                              # 项目根
    ICON_PATH = ROOT / "logo.ico"
    DATA_DIR = ROOT

if getattr(sys, "frozen", False):
    CACHE_DIR = DATA_DIR / ".cache"                 # 缩略图等缓存
    QT_PROFILE = DATA_DIR / "user_data_qt"          # QtWebEngine 持久化登录态目录
    SETTINGS_FILE = DATA_DIR / "settings.json"
else:
    CACHE_DIR = ROOT / ".cache"                     # 开发模式沿用项目目录
    QT_PROFILE = ROOT / "user_data_qt"
    SETTINGS_FILE = ROOT / ".settings.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

APP_ID = "DyCollector"
QT_CDP_PORT = 9357                                  # QtWebEngine 内嵌引擎 CDP 端口（与 XHS 的 9347 隔离）
CHROMIUM_FLAGS = "--remote-allow-origins=*"

HOME = "https://www.douyin.com"
FAVORITE_URL = HOME + "/user/self?showTab=favorite_collection"   # 本人收藏页（需登录）

# Cookie 本地持久化：判定“已登录”的会话键（出现任一即视为登录态）
LOGIN_COOKIE_KEYS = ("sessionid", "sid_guard", "sessionid_ss")
COOKIE_DOMAIN_MARK = "douyin.com"
COOKIES_FILE = DATA_DIR / "cookies.json"            # Cookie 快照（测试/后台 HTTP 通道统一复用）
HISTORY_FILE = DATA_DIR / "history.json"            # 抓取历史（预留）
HISTORY_MAX = 300
TASKS_FILE = DATA_DIR / "tasks.json"                # 下载任务队列（预留）
TASKS_MAX = 1000

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 采集相关（对齐 XHS 经验值）
MAX_ITEMS = 500                                     # 单次抓取上限（防御）
SCROLL_ROUNDS = 60                                  # 滚动轮数上限
EMPTY_STOP = 12                                     # 连续无新作品 N 轮才判定到底
# 收割监听的接口关键字（收藏页三类响应 + 主页作品流；字段以运行时实测为准）
LISTEN_KEYWORDS = [
    "aweme/post",
    "aweme/favorite",
    "listcollection",
    "collects/list",
    "collects/video/list",
    "aweme/detail",
]
