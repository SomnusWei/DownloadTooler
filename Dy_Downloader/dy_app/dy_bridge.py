# -*- coding: utf-8 -*-
"""page_bridge —— 借主进程「已登录页面」发同源请求，绕过抖音 Argus 门禁

抖音自 2026-09 起对 /aweme/v1/web/aweme/detail/ 等接口加 Argus 风控：
非页面请求（aiohttp/urllib 直连）即便签名与 Cookie 都正确，也会被
``403 Blocked by ArgusSecurityPlugin`` 拦掉；而在 www.douyin.com 页面内
发起的同源 fetch 则放行。桌面版由 Electron 主进程的隐藏登录视图承担这一角色。

协议（行式，stdout/stdin）：
  引擎  → stdout : ``==DYC_BRIDGE_REQ== {"id":1,"url":"...","method":"GET"}``
  主进程 → stdin  : ``==DYC_BRIDGE_RES== {"id":1,"http_status":200,"text":"..."}``
仅当主进程托管（环境变量 DYC_BRIDGE=1）时可用；独立运行时自动跳过。
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import sys
import threading
import time
from typing import Any, Dict

_REQ_PREFIX = "==DYC_BRIDGE_REQ=="
_RES_PREFIX = "==DYC_BRIDGE_RES=="

_seq = itertools.count(1)
_lines: "queue.Queue[str]" = queue.Queue()
_reader_started = False
_reader_lock = threading.Lock()


def available() -> bool:
    """是否由主进程托管（可走页面通道）。"""
    return os.environ.get("DYC_BRIDGE") == "1" and sys.stdout is not None and sys.stdin is not None


def _read_stdin_loop() -> None:
    # 逐行读取；单行异常不终止线程（大应答/编码问题不应让整个通道失效）
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            continue
        if not line:
            return
        _lines.put(line)


def _ensure_reader() -> None:
    global _reader_started
    with _reader_lock:
        if not _reader_started:
            _reader_started = True
            threading.Thread(target=_read_stdin_loop, daemon=True).start()


def request(url: str, method: str = "GET", body: str = "", timeout: float = 30.0) -> Dict[str, Any]:
    """在页面上下文里发起请求，返回 {http_status, text}；失败抛 RuntimeError。"""
    if not available():
        raise RuntimeError("page_bridge 不可用（引擎未由主进程托管）")
    _ensure_reader()
    rid = next(_seq)
    payload = {"id": rid, "url": url, "method": method}
    if body:
        payload["body"] = body
    print(_REQ_PREFIX + " " + json.dumps(payload, ensure_ascii=False), flush=True)

    deadline = time.monotonic() + timeout
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise RuntimeError("page_bridge 超时")
        try:
            line = _lines.get(timeout=min(remain, 1.0))
        except queue.Empty:
            continue
        text = (line or "").strip()
        if not text.startswith(_RES_PREFIX):
            continue
        try:
            data = json.loads(text[len(_RES_PREFIX):].strip())
        except Exception:
            continue
        if str(data.get("id")) != str(rid):
            continue
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        print("[bridge] id=%s http=%s bytes=%s" % (
            rid, data.get("http_status"), len(data.get("text") or "")), flush=True)
        return data
