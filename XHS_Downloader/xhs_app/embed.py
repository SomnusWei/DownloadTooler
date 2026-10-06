# -*- coding: utf-8 -*-
"""内嵌浏览器引擎：PySide6 QtWebEngine（Chromium 内核）

- 作为 GUI 左侧常驻页面，登录/滑块验证等人工操作直接在内嵌页完成；
- 通过 CDP（QTWEBENGINE_REMOTE_DEBUGGING）让 DrissionPage 挂接同一页面做自动化；
- Cookie 持久化到独立 profile（user_data_qt），重启免登录。

注意：CDP 端口与 Chromium 启动参数必须在 QtWebEngine 首次初始化前设置，
本模块在 import 时即设置环境变量。
"""
import os
import threading
import time

from xhs_app import config

# GPU 默认启用（硬件合成）。早期为稳妥加过 --disable-gpu，代价是整页变
# 软件渲染、滚动/动效卡顿；现代 Edge/GPU 驱动下默认即可，异常时再由
# config.CHROMIUM_FLAGS 覆盖关掉。
os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", str(config.QT_CDP_PORT))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      getattr(config, "CHROMIUM_FLAGS", "--remote-allow-origins=*"))

from PySide6.QtCore import QUrl, Signal  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402

# 右键命中检测：① 光标所在的最小笔记卡片链接 ② 最近 <a> ③ 卡片容器内的首个笔记链接
# （兼容 作者主页/首页推荐流/搜索页 三种不同卡片结构）
_CTX_JS = r"""
(function(x, y) {
  function isN(h) {
    return !!h && /xiaohongshu\.com\/(?:explore\/|discovery\/item\/|user\/profile\/[^\/]+\/)[0-9a-f]+/i.test(h);
  }
  var best = "", bestArea = Infinity;
  var all = document.querySelectorAll("a[href]");
  for (var i = 0; i < all.length; i++) {
    var h = all[i].href;
    if (!isN(h)) continue;
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
    if (a0 && isN(a0.href)) return a0.href;
    var n = el;
    for (var j = 0; n && j < 10; j++, n = n.parentElement) {
      var tag = (n.tagName || "").toLowerCase();
      var cls = n.className ? n.className.toString() : "";
      if (tag === "section" || /note|feed|card|item/i.test(cls)) {
        var links = n.querySelectorAll ? n.querySelectorAll("a[href]") : [];
        for (var m = 0; m < links.length; m++) {
          if (isN(links[m].href)) return links[m].href;
        }
        return "";
      }
    }
  }
  return "";
})(__X__, __Y__);
"""


class EngineView(QWebEngineView):
    """内嵌浏览器视图（必须创建在 Qt 主线程）。

    右键任意笔记卡片封面 → 菜单出现「下载该笔记」，可直接把这篇加入下载队列。
    """

    requestDownloadNote = Signal(str)  # 右键菜单发出的“下载某篇笔记”请求

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = QWebEngineProfile("xhs_main")
        self._profile.setPersistentStoragePath(str(config.QT_PROFILE))
        self._profile.setCachePath(str(config.QT_PROFILE / "cache"))
        self._profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
        self._profile.setHttpCacheMaximumSize(512 * 1024 * 1024)
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        # 默认 UA 带 QtWebEngine 标识，易被平台判定为自动化而"登录即掉"，
        # 这里覆盖成普通 Chrome UA 以降低指纹
        try:
            self._profile.setHttpUserAgent(config.UA)
        except Exception:
            pass
        # 非活动页后台节流会让 XHS 滚动加载变慢/中断，这里禁掉以保抓取稳定
        try:
            self._profile.setBackgroundTimerThrottlingPolicy(
                QWebEngineProfile.DisallowTimerThrottlingForBackgroundPages)
        except Exception:
            pass
        page = QWebEnginePage(self._profile, self)
        # 小红书大量卡片用 target=_blank/新窗口跳转；单窗口体验改为在当前视图内打开
        page.newWindowRequested.connect(self._on_new_window)
        self.setPage(page)
        self.load(QUrl(config.HOME + "/explore"))

    def _on_new_window(self, request):
        try:
            request.openIn(self.page())
        except Exception:
            pass  # 打开失败则忽略，避免阻断页面内其它操作

    def open_url(self, url):
        self.load(QUrl(url))

    # ---------- 右键笔记卡片 → 下载该篇 ----------
    def contextMenuEvent(self, event):
        """接管右键：命中笔记卡片时提供「下载该笔记」（替代 Chromium 默认菜单）"""
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
                act = QAction("⬇ 下载该笔记的图片/视频", menu)
                act.triggered.connect(lambda: self.requestDownloadNote.emit(href))
                menu.addAction(act)
                copy = QAction("复制笔记链接", menu)
                copy.triggered.connect(
                    lambda: self._copy(href))
                menu.addAction(copy)
            else:
                no = QAction("此处未识别到笔记卡片（右键封面小图）", menu)
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
