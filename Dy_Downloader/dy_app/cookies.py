# -*- coding: utf-8 -*-
"""Cookie 本地持久化（本方案强制项，见方案.md §3.3.1）

设计（双保险）：
1. QtWebEngine profile（user_data_qt）已持久化全部 Cookie（含 httpOnly）→ 重启免登录；
2. cookies.json 快照 → 供自动化测试 / 后台 HTTP 下载通道统一复用，
   避免测试过程中反复扫码登录。

判定“已登录”：Cookie 中出现 config.LOGIN_COOKIE_KEYS 任一非空会话键。
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from dy_app import config


# ---------------- 提取 ----------------
def extract_from_tab(tab) -> Dict[str, str]:
    """从 DrissionPage 挂接的页面提取全部 Cookie → {name: value}。

    兼容 DrissionPage 4.x `tab.cookies`（dict / 可迭代 dict 两种形态）。
    """
    out: Dict[str, str] = {}
    raw = None
    attr = getattr(tab, "cookies", None)
    if callable(attr):                       # DrissionPage 4.x：cookies 为方法
        try:
            raw = attr()
        except Exception:
            raw = None
    else:
        raw = attr
    if isinstance(raw, dict):
        out = {str(k): ("" if v is None else str(v)) for k, v in raw.items()}
    elif raw is not None:
        for c in raw:
            if isinstance(c, dict) and c.get("name") is not None:
                v = c.get("value")
                out[str(c["name"])] = "" if v is None else str(v)
    return out


def filter_domain(cookies: Dict[str, str]) -> Dict[str, str]:
    """仅保留抖音域的关键 Cookie（name 级别过滤，防止污染其它站点快照）。"""
    keep = ("sessionid", "sid_guard", "sid_tt", "uid_tt", "ttwid", "odin_tt",
            "passport_csrf_token", "msToken", "passport_auth_status",
            "passport_fe_beating_status", "msToken")
    return {k: v for k, v in cookies.items() if k.startswith("sessionid")
            or k.startswith("sid_") or k.startswith("uid_")
            or k.startswith("passport_") or k.startswith("tt_")
            or k in keep}


# ---------------- 登录判定 ----------------
def has_login(cookies: Optional[Dict[str, str]]) -> bool:
    if not cookies:
        return False
    return any(cookies.get(k) for k in config.LOGIN_COOKIE_KEYS)


# ---------------- 持久化存取 ----------------
class CookieKeeper:
    """cookies.json 快照的读写（路径默认 %DATA_DIR%\\cookies.json）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else Path(config.COOKIES_FILE)

    def load(self) -> Dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            cookies = data.get("cookies") or {}
            return cookies if isinstance(cookies, dict) else {}
        except Exception:
            return {}

    def save(self, cookies: Dict[str, str]) -> Path:
        """覆盖写快照；不存任何 name 为空/过期痕迹的键。"""
        clean = {k: ("" if v is None else str(v)) for k, v in cookies.items() if k}
        payload = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "login": has_login(clean),
            "cookies": clean,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        return self.path

    def has_saved_login(self) -> bool:
        return has_login(self.load())

    # ---- 供后台 HTTP 通道 / 测试复用的输出形态 ----
    def load_header_str(self) -> str:
        return self.to_header_str(self.load())

    @staticmethod
    def to_header_str(cookies: Dict[str, str]) -> str:
        return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

    def load_cookie_dict(self) -> Dict[str, str]:
        return {k: v for k, v in self.load().items() if v}
