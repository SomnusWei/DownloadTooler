# -*- coding: utf-8 -*-
"""spike_dy_e —— Electron 双栈收割/Cookie 探针（M0-Electron 版）

前提：Electron 壳已启动（electron/src/main.js，CDP 端口 9357）。
流程（全部复用本地持久化 Cookie，免反复扫码）：
  1. 通过 DrissionPage 挂接 Electron 渲染进程 CDP；
  2. 检查登录态（持久化优先）→ 未登录则提示在 Electron 窗口扫码；
  3. 落盘 cookies.json 快照（供测试/后台 HTTP 下载通道复用）；
  4. 收割试验：进入收藏页，监听分页 XHR，统计命中接口与作品条目。

用法：
  终端模式：  .\\.venv\\Scripts\\python.exe spike_dy_e.py [--url https://...]
  日志窗口：  .\\.venv\\Scripts\\python.exe spike_dy_e.py --gui [--url https://...]
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dy_app import config, cookies as cookies_mod  # noqa: E402

TARGET_DEFAULT = config.FAVORITE_URL
LOGIN_WAIT = 300
SCROLL_ROUNDS = 12
SCROLL_STEP = 1600


def attach():
    """挂接 Electron(127.0.0.1:9357) 的 Chromium CDP。"""
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions().set_address(f"127.0.0.1:{config.QT_CDP_PORT}")
    last = None
    for _ in range(30):
        try:
            return ChromiumPage(co)
        except Exception as e:      # DevTools/窗口未就绪时重试
            last = e
            time.sleep(0.5)
    raise RuntimeError(f"无法挂接 Electron CDP（先启动 electron: npm start）：{last}")


def to_dict(body):
    if isinstance(body, dict):
        return body
    if isinstance(body, (str, bytes)):
        try:
            return json.loads(body)
        except Exception:
            return {}
    return {}


def deep_list(data, key):
    if not isinstance(data, dict):
        return []
    v = data.get(key) or []
    return v if isinstance(v, list) else []


def run_probe(target: str, out, cancel_event=None) -> int:
    """探针主流程；out(msg) 为日志输出通道；cancel_event 置位时尽早退出。"""
    keeper = cookies_mod.CookieKeeper()
    out(f"[spike_e] 目标: {target or '(默认)'}")
    out("[1/4] 挂接 Electron CDP …")
    try:
        tab = attach()
    except Exception as e:
        out(f"[失败] {e}")
        return 1
    out("       ✓ 已挂接（当前: " + str(tab.url) + "）")

    # ---- 登录态（持久化优先）----
    out("[2/4] 检查登录态（本地持久化优先）…")
    snap = cookies_mod.extract_from_tab(tab)
    if cookies_mod.has_login(snap):
        out("       ✓ Electron 已带登录态 —— userData session 持久化恢复，免扫码")
    else:
        out("       未登录。请在 Electron 窗口内扫码登录抖音…")
        try:
            tab.get(config.FAVORITE_URL)
        except Exception:
            pass
        deadline = time.time() + LOGIN_WAIT
        while time.time() < deadline:
            if cancel_event and cancel_event.is_set():
                out("[中止] 用户取消。")
                return -1
            snap = cookies_mod.extract_from_tab(tab)
            if cookies_mod.has_login(snap):
                break
            time.sleep(2)
        if cookies_mod.has_login(snap):
            out("       ✓ 登录成功")
        else:
            out("[中止] 等待扫码超时。")
            return 2
    keeper.save(snap)
    out(f"       Cookie 快照已保存: {keeper.path}（{len(snap)} 条；"
        f"会话键: {[k for k in ('sessionid', 'sid_guard', 'sessionid_ss') if snap.get(k)]}）")

    # ---- 收割试验 ----
    out("[3/4] 收割试验：监听分页 XHR + 滚动…")
    try:
        tab.listen.start(config.LISTEN_KEYWORDS)
    except Exception as e:
        out(f"[失败] 启动监听失败: {e}")
        return 3
    try:
        tab.get(target or config.FAVORITE_URL)
        out("       已导航: " + str(tab.url))
    except Exception as e:
        out(f"       导航异常(继续尝试滚动): {e}")
    time.sleep(4)

    hits, aweme_count, collect_count, samples = {}, 0, 0, []

    def drain(timeout=0.5):
        nonlocal aweme_count, collect_count
        try:
            for p in tab.listen.steps(timeout=timeout):
                url = ""
                try:
                    url = p.url or ""
                except Exception:
                    pass
                if not url:
                    try:
                        url = getattr(p.request, "url", "") or ""
                    except Exception:
                        pass
                key = url.rstrip("/").rsplit("/", 1)[-1] if url else "?"
                if not url or not key:      # 过滤无意义包
                    continue
                hits[key] = hits.get(key, 0) + 1
                body = to_dict(getattr(p.response, "body", None))
                data = body.get("data") or {}
                awemes = deep_list(data, "aweme_list") or deep_list(body, "aweme_list")
                if awemes:
                    aweme_count += len(awemes)
                    if len(samples) < 5:
                        it = awemes[0] or {}
                        samples.append({
                            "aweme_id": it.get("aweme_id"),
                            "desc": (it.get("desc") or "")[:40],
                            "images": bool(it.get("images")),
                            "video": bool(it.get("video")),
                            "create_time": it.get("create_time"),
                        })
                collect_count += len(deep_list(data, "collects_list"))
        except Exception as e:
            out(f"       [诊断] drain 异常: {type(e).__name__}: {e}")

    drain(1.0)
    for _ in range(SCROLL_ROUNDS):
        if cancel_event and cancel_event.is_set():
            out("[中止] 用户取消。")
            return -1
        try:
            tab.scroll.down(SCROLL_STEP)
        except Exception:
            pass
        time.sleep(0.9)
        drain(0.35)

    # ---- 汇总 ----
    out("[4/4] 汇总")
    if hits:
        out("       命中接口: " + json.dumps(hits, ensure_ascii=False))
    else:
        out("       未捕获目标接口（可能需在页面点击收藏夹后再跑）")
    out(f"       作品条目: {aweme_count}；收藏夹条目: {collect_count}")
    if samples:
        out("       作品样本: " + json.dumps(samples, ensure_ascii=False, indent=1))
    out(f"[结论] Electron 收割链路{'✓' if aweme_count else '待补'} | 接口 {len(hits)} 类 | 作品 {aweme_count}")
    return 0


# ---------------- GUI 日志模式 ----------------
def run_gui(target: str) -> int:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel,
                                   QMainWindow, QPlainTextEdit, QPushButton,
                                   QVBoxLayout, QWidget)

    class Sig(QObject):
        log = Signal(str)
        done = Signal(int)

    app = QApplication(sys.argv)
    sig = Sig()
    cancel = threading.Event()

    win = QMainWindow()
    win.setWindowTitle("DyCollector · spike_dy_e 探针工作日志")
    try:
        win.setWindowIcon(QIcon(str(config.ICON_PATH)))
    except Exception:
        pass
    win.resize(920, 680)

    central = QWidget(win)
    box = QVBoxLayout(central)
    tip = QLabel("请保持 Electron(DyCollector) 窗口开着；未登录时在 Electron 窗口内扫码。日志实时显示如下：")
    tip.setWordWrap(True)
    box.addWidget(tip)
    log_view = QPlainTextEdit(central)
    log_view.setReadOnly(True)
    log_view.setMaximumBlockCount(5000)
    box.addWidget(log_view, 1)
    btns = QHBoxLayout()
    stop_btn = QPushButton("停止", central)
    btns.addStretch(1)
    btns.addWidget(stop_btn)
    box.addLayout(btns)
    win.setCentralWidget(central)

    def on_log(msg):
        log_view.appendPlainText(msg)
        sb = log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_done(code):
        on_log(f"[探针结束] 退出码 {code}（可关闭本窗口）")

    sig.log.connect(on_log)
    sig.done.connect(on_done)
    stop_btn.clicked.connect(lambda: (cancel.set(), on_log("[用户] 请求停止…")))
    win.closeEvent = lambda _e: app.quit()

    def worker():
        code = run_probe(target, lambda m: sig.log.emit(m), cancel)
        sig.done.emit(code)

    threading.Thread(target=worker, daemon=True).start()
    win.show()
    return app.exec()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="试验目标 URL（默认本人收藏页）")
    ap.add_argument("--gui", action="store_true", help="弹出工作日志窗口（默认终端输出）")
    args = ap.parse_args()
    if args.gui:
        return run_gui(args.url)
    return run_probe(args.url, lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
