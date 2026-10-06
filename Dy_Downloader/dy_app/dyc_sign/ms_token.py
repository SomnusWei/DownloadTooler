# -*- coding: utf-8 -*-
"""msToken 生成（移植 douyin-downloader 的方案）

抖音 web 接口要求 Cookie/查询参数里带 msToken：
  1) 优先向 mssdk.bytedance.com 申请真实 token（六字段配置快照见
     ``f2_ms_token_conf.py``，来自 F2 项目 conf.yaml，MIT）；
  2) 申请失败（无网络/上游变更）时回落随机 token，保证请求参数完整。

带进程内按 Cookie+UA 维度的缓存（60s）与失败退避（300s），避免每个请求
都去探测上游把主流程拖慢。
"""
from __future__ import annotations

import hashlib
import json
import random
import string
import time
import urllib.request
from http.cookies import SimpleCookie
from threading import Lock
from typing import Any, Dict, Optional, Tuple

try:
    from dy_app.dyc_sign.f2_ms_token_conf import BUNDLED_MS_TOKEN_CONF
except Exception:                                   # pragma: no cover - 快照缺失时仅剩随机兜底
    BUNDLED_MS_TOKEN_CONF = {}

_PROBE_TIMEOUT_S = 3.0          # 上游探测的短预算，避免拖慢下载主流程
_TOKEN_TTL_S = 60.0             # 同作用域内复用真实 token 的时间
_FAIL_BACKOFF_S = 300.0         # 一次失败后，这段时间内直接用随机 token

_tokens: Dict[str, Tuple[float, str]] = {}
_lock = Lock()
_retry_after = 0.0


def _is_valid(token: Optional[str]) -> bool:
    """与 F2 一致：真实 msToken 长度通常为 164 或 184。"""
    return bool(token) and isinstance(token, str) and len(token.strip()) in (164, 184)


def random_ms_token() -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(182)) + "=="


def _scope_key(cookies: Dict[str, str], user_agent: str) -> str:
    payload = json.dumps(
        {"cookies": cookies or {}, "user_agent": user_agent},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _gen_real_ms_token(user_agent: str) -> Optional[str]:
    conf: Dict[str, Any] = BUNDLED_MS_TOKEN_CONF
    if not conf:
        return None
    payload = {
        "magic": conf.get("magic"),
        "version": conf.get("version"),
        "dataType": conf.get("dataType"),
        "strData": conf.get("strData"),
        "ulr": conf.get("ulr"),
        "tspFromClient": int(time.time() * 1000),
    }
    req = urllib.request.Request(
        conf.get("url", ""),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            for header in (resp.headers.get_all("Set-Cookie") or []):
                jar = SimpleCookie()
                jar.load(header)
                morsel = jar.get("msToken")
                if morsel and _is_valid(morsel.value):
                    return morsel.value.strip()
    except Exception:
        return None
    return None


def ensure_ms_token(cookies: Dict[str, str], user_agent: str) -> str:
    """返回可用的 msToken：Cookie 已有则原样返回，否则生成（真实优先，随机兜底）。"""
    current = (cookies or {}).get("msToken", "").strip()
    if current:
        return current
    global _retry_after
    key = _scope_key(cookies, user_agent)
    with _lock:
        now = time.monotonic()
        expired = [k for k, (exp, _t) in _tokens.items() if exp <= now]
        for k in expired:
            _tokens.pop(k, None)
        hit = _tokens.get(key)
        if hit:
            return hit[1]
        if now < _retry_after:
            return random_ms_token()
        real = _gen_real_ms_token(user_agent)
        if real:
            _retry_after = 0.0
            _tokens[key] = (time.monotonic() + _TOKEN_TTL_S, real)
            return real
        _retry_after = time.monotonic() + _FAIL_BACKOFF_S
        return random_ms_token()
