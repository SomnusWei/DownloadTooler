# -*- coding: utf-8 -*-
"""spike_dy_a —— 抖音收割可行性 + Cookie 本地持久化 探针（M0）

验证目标（全程优先走本地持久化 Cookie，避免反复扫码）：
1. QtWebEngine profile(user_data_qt) 重启免登录：二次运行时浏览器内已带登录态；
2. cookies.json 快照：登录后自动落盘，供测试/后台 HTTP 通道复用；
3. 收割可行性：挂接内嵌浏览器，进入收藏页/主页，滚动监听分页 XHR，
   统计命中接口与作品条目，验证“列表响应自带播放地址、无需逐篇详情”。

用法：
  .\\.venv\\Scripts\\python.exe spike_dy_a.py [--url https://...]
  # 默认目标 = 本人收藏页（需登录）；也可传任意作者主页 / 作品链接观察接口形态
"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,  # noqa: E402
                               QMainWindow, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from dy_app import config, cookies as cookies_mod  # noqa: E402
from dy_app.embed import EngineView  # noqa: E402

TARGET_DEFAULT = config.FAVORITE_URL
LOGIN_WAIT = 300          # 等待扫码上限（秒）
SCROLL_ROUNDS = 12        # 收割试验滚动轮数
SCROLL_STEP = 1600


def _to_dict(body):
    if isinstance(body, dict):
        return body
    if isinstance(body, (str, bytes)):
        try:
            return json.loads(body)
        except Exception:
            return {}
    return {}


def _deep_list(data, key):
    if not isinstance(data, dict):
        return []
    v = data.get(key) or []
    return v if isinstance(v, list) else []


class _Signals(QObject):
    log = Signal(str)
    done = Signal(str)


class SpikeWindow(QMainWindow):
    """内嵌浏览器 + 探针日志/控制台"""

    def __init__(self, target: str):
        super().__init__()
        self.setWindowTitle("DyCollector · spike_dy_a 探针")
        try:
            self.setWindowIcon(QIcon(str(config.ICON_PATH)))
        except Exception:
            pass
        self.resize(1280, 900)
        self._sig = _Signals()
        self._sig.log.connect(self._append)

        central = QWidget(self)
        box = QVBoxLayout(central)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("目标(留空=默认收藏页):"))
        self._url = QLineEdit(target or TARGET_DEFAULT, self)
        self._start = QPushButton("开始探针", self)
        self._start.clicked.connect(self._begin)
        bar.addWidget(self._url, 1)
        bar.addWidget(self._start)
        box.addLayout(bar)
        self._view = EngineView(self)
        self._view.setMinimumHeight(520)
        box.addWidget(self._view, 3)
        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)
        box.addWidget(self._log, 1)
        self.setCentralWidget(central)
        self.statusBar().addWidget(
            QLabel(f"Cookie 持久化目录：{config.QT_PROFILE}　快照：{config.COOKIES_FILE}"))
        QTimer.singleShot(600, self._begin)

    # ---------- UI 辅助 ----------
    def _append(self, text: str):
        self._log.appendPlainText(text)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
        # 同步打到 stdout，便于终端/自动化读取探针结论
        print(text, flush=True)

    def _begin(self):
        if self._start.isEnabled():
            self._start.setEnabled(False)
        threading.Thread(target=self._run, args=(self._url.text().strip(),), daemon=True).start()

    # ---------- 探针主流程（后台线程） ----------
    def _run(self, target: str):
        log = self._sig.log
        keeper = cookies_mod.CookieKeeper()
        log.emit(f"[探针] 目标: {target or '(默认)'}")
        try:
            tab = self._view.attach()
        except Exception as e:
            log.emit(f"[失败] 无法挂接内嵌浏览器：{e}\n      请先关闭其它 DyCollector/spike 进程。")
            self._sig.done.emit("attach-failed")
            return

        # ---------- 1) 登录态检查 / 等待扫码 ----------
        log.emit("[1/3] 检查登录态（本地持久化优先）…")
        snap = cookies_mod.extract_from_tab(tab)
        if cookies_mod.has_login(snap):
            log.emit("      ✓ 浏览器已带登录态 —— 来自 user_data_qt 持久化恢复，免扫码")
            keeper.save(snap)
        else:
            log.emit("      未检测到登录态，请在弹出的内嵌页面扫码登录（抖音右上角头像→扫码）。")
            log.emit("      正在打开收藏页以触发登录引导…")
            try:
                tab.get(config.FAVORITE_URL)
            except Exception as e:
                log.emit(f"      导航收藏页失败（继续等待登录）：{e}")
            deadline = time.time() + LOGIN_WAIT
            while time.time() < deadline:
                snap = cookies_mod.extract_from_tab(tab)
                if cookies_mod.has_login(snap):
                    break
                time.sleep(2)
            if cookies_mod.has_login(snap):
                log.emit("      ✓ 登录成功")
            else:
                log.emit("[中止] 等待扫码超时，未登录无法访问收藏页。")
                self._sig.done.emit("no-login")
                return

        p = keeper.save(snap)
        keys = [k for k in ("sessionid", "sid_guard", "sessionid_ss") if snap.get(k)]
        log.emit(f"      Cookie 快照已保存: {p}  （{len(snap)} 条；会话键: {keys}）")

        # ---------- 2) 收割可行性试验 ----------
        log.emit("[2/3] 收割试验：监听分页 XHR + 滚动…")
        url = target or config.FAVORITE_URL
        try:
            tab.listen.start(config.LISTEN_KEYWORDS)
        except Exception as e:
            log.emit(f"[失败] 启动监听失败：{e}")
            self._sig.done.emit("listen-failed")
            return

        try:
            tab.get(url)
            log.emit(f"      已导航: {tab.url if hasattr(tab, 'url') else url}")
        except Exception as e:
            log.emit(f"      导航异常(继续尝试滚动): {e}")
        time.sleep(4)

        hits = {}          # 接口路径 -> 包数
        aweme_count = 0
        collect_count = 0
        samples = []       # 作品样本（标题/aweme_id/类型）
        diag_shown = [False]

        def packet_info(p):
            """DrissionPage 4.x DataPacket 的 url/body 提取（带容错）"""
            url = ""
            try:
                url = p.url or ""
            except Exception:
                url = ""
            if not url:
                try:
                    url = getattr(p.request, "url", "") or ""
                except Exception:
                    url = ""
            body = _to_dict(getattr(p.response, "body", None))
            return url, body

        def drain(timeout=0.5):
            nonlocal aweme_count, collect_count
            try:
                for p in tab.listen.steps(timeout=timeout):
                    url, body = packet_info(p)
                    if not url and not diag_shown[0]:
                        diag_shown[0] = True
                        print("   [诊断] 空URL数据包: " + repr(p)[:400], flush=True)
                    from urllib.parse import urlparse
                    path = urlparse(url).path if url else ""
                    key = path.rstrip("/").rsplit("/", 1)[-1] if path else "?"
                    hits[key] = hits.get(key, 0) + 1
                    data = body.get("data") or {}
                    awemes = _deep_list(data, "aweme_list")
                    if not awemes:  # 兼容部分端点结构差异
                        awemes = _deep_list(body, "aweme_list")
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
                    collect_count += len(_deep_list(data, "collects_list"))
            except Exception as e:
                print(f"   [诊断] drain 异常: {type(e).__name__}: {e}", flush=True)

        drain(1.0)
        for _ in range(SCROLL_ROUNDS):
            try:
                tab.scroll.down(SCROLL_STEP)
            except Exception:
                pass
            time.sleep(0.9)
            drain(0.35)

        # ---------- 3) 汇总 ----------
        log.emit("[3/3] 汇总")
        if hits:
            log.emit("      命中接口计数: " + json.dumps(hits, ensure_ascii=False))
        else:
            log.emit("      未捕获到目标接口 —— 收藏页可能未自动加载/或需点击收藏夹（结论见下）。")
        log.emit(f"      作品条目合计(本页捕获): {aweme_count}；收藏夹条目: {collect_count}")
        if samples:
            log.emit("      作品样本: " + json.dumps(samples, ensure_ascii=False, indent=1))
        summary = (f"登录方式={'持久化恢复' if keeper.load().get('saved_at') else '本次扫码'} | "
                   f"捕获接口 {len(hits)} 类 | 作品 {aweme_count} | 收藏夹 {collect_count}")
        log.emit(f"[结论] {summary}")
        self._sig.done.emit(summary)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="试验目标 URL（默认本人收藏页）")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    win = SpikeWindow(args.url)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
