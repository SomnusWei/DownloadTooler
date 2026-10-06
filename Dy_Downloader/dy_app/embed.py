# -*- coding: utf-8 -*-
"""内嵌浏览器引擎：PySide6 QtWebEngine（Chromium 内核）

- 作为 GUI 左侧常驻页面，抖音登录/滑块验证等人工操作直接在内嵌页完成；
- 通过 CDP（QTWEBENGINE_REMOTE_DEBUGGING）让 DrissionPage 挂接同一页面做自动化；
- Cookie 持久化到独立 profile（user_data_qt），重启免登录 —— 配合 cookies.json 快照，
  让所有测试/后台通道统一复用本地持久化 Cookie，避免反复扫码。

注意：CDP 端口与 Chromium 启动参数必须在 QtWebEngine 首次初始化前设置，
本模块在 import 时即设置环境变量。
"""
import os
import time

from dy_app import config

os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", str(config.QT_CDP_PORT))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", config.CHROMIUM_FLAGS)

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402

# 右键“识别当前页作品链接”判定：命中 douyin 作品/图文 卡片才给复制/入口
_CTX_JS = r"""
(function(x, y) {
  var isW = function(h) {
    return !!h && /(?:www\.)?douyin\.com\/(?:video|note|gallery|collection|mix)\/[0-9]+/i.test(h);
  };
  var best = "", bestArea = Infinity, all = document.querySelectorAll("a[href]");
  for (var i = 0; i < all.length; i++) {
    var h = all[i].href;
    if (!isW(h)) continue;
    var r = all[i].getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
      var area = (r.right - r.left) * (r.bottom - r.top);
      if (area < bestArea) { bestArea = area; best = h; }
    }
  }
  if (best) return best;
  var el = document.elementFromPoint(x, y);
  if (el && el.closest) {
    var a0 = el.closest("a");
    if (a0 && isW(a0.href)) return a0.href;
    var n = el;
    for (var j = 0; n && j < 10; j++, n = n.parentElement) {
      var tag = (n.tagName || "").toLowerCase();
      var cls = n.className ? n.className.toString() : "";
      if (tag === "section" || /aweme|card|item|feed|note/i.test(cls)) {
        var links = n.querySelectorAll ? n.querySelectorAll("a[href]") : [];
        for (var m = 0; m < links.length; m++) {
          if (isW(links[m].href)) return links[m].href;
        }
        return "";
      }
    }
  }
  return "";
})(__X__, __Y__);
"""


class EngineView(QWebEngineView):
    """内嵌浏览器视图（必须创建在 Qt 主线程）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = QWebEngineProfile("dy_main")
        self._profile.setPersistentStoragePath(str(config.QT_PROFILE))
        self._profile.setCachePath(str(config.QT_PROFILE / "cache"))
        self._profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
        self._profile.setHttpCacheMaximumSize(512 * 1024 * 1024)
        # ForcePersistentCookies：全部 Cookie（含 httpOnly）落到 user_data_qt，重启免登录
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        try:
            self._profile.setHttpUserAgent(config.UA)
        except Exception:
            pass
        # 后台节流会拖慢滚动加载，抓取期禁掉
        try:
            self._profile.setBackgroundTimerThrottlingPolicy(
                QWebEngineProfile.DisallowTimerThrottlingForBackgroundPages)
        except Exception:
            pass
        page = QWebEnginePage(self._profile, self)
        # 新窗口请求统一在当前视图打开，避免抓取期间弹新窗
        page.newWindowRequested.connect(self._on_new_window)
        self.setPage(page)
        self.load(QUrl(config.HOME))

    def _on_new_window(self, request):
        try:
            request.openIn(self.page())
        except Exception:
            pass

    def open_url(self, url):
        self.load(QUrl(url))

    # ---------- 右键：复制作品链接（完整版右键“下载本作品”在 M1 接入） ----------
    def contextMenuEvent(self, event):
        try:
            pt = event.position().toPoint()
        except Exception:
            pt = event.pos()
        self._ctx_pos = pt
        try:
            self.page().runJavaScript(
                _CTX_JS.replace("__X__", str(pt.x())).replace("__Y__", str(pt.y())),
                self._on_ctx_hit)
        except Exception:
            pass

    def _on_ctx_hit(self, href):
        try:
            href = (href or "").strip()
            menu = QMenu(self)
            if href:
                copy = QAction("复制作品链接", menu)
                copy.triggered.connect(lambda: self._copy(href))
                menu.addAction(copy)
            else:
                no = QAction("此处未识别到作品卡片", menu)
                no.setEnabled(False)
                menu.addAction(no)
            menu.addSeparator()
            back = QAction("后退", menu)
            back.setEnabled(self.page() is not None and self.page().history().canGoBack())
            back.triggered.connect(lambda: self.page().history().back())
            menu.addAction(back)
            fwd = QAction("前进", menu)
            fwd.setEnabled(self.page() is not None and self.page().history().canGoForward())
            fwd.triggered.connect(lambda: self.page().history().forward())
            menu.addAction(fwd)
            ref = QAction("刷新页面", menu)
            ref.triggered.connect(self.reload)
            menu.addAction(ref)
            menu.exec(self.mapToGlobal(self._ctx_pos))
            menu.deleteLater()
        except Exception:
            pass

    def _copy(self, text):
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            pass

    def attach(self):
        """在工作线程中挂接本引擎的 CDP，返回 DrissionPage 控制对象。

        引擎跑在 Qt 主线程，这里只是通过 CDP 会话下发指令，二者互不阻塞。
        """
        from DrissionPage import ChromiumOptions, ChromiumPage

        co = ChromiumOptions().set_address(f"127.0.0.1:{config.QT_CDP_PORT}")
        last = None
        for i in range(25):
            try:
                return ChromiumPage(co)
            except Exception as e:  # 引擎 DevTools 可能尚未就绪，重试
                last = e
                time.sleep(0.4)
        raise RuntimeError(f"无法挂接内嵌浏览器：{last}")
