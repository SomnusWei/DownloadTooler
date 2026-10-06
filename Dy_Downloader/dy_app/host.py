# -*- coding: utf-8 -*-
"""轻量浏览器宿主窗口（M1 完整面板接入前的过渡宿主）

能力：内嵌浏览器 + 地址栏导航 + 一键「我的收藏」+ 手动「保存 Cookie 快照」。
登录态由 QtWebEngine profile(user_data_qt) 自动持久化；快照供测试与后台通道复用。
"""
import threading

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPushButton, QToolBar, QVBoxLayout,
                               QWidget)

from dy_app import config, cookies as cookies_mod
from dy_app.embed import EngineView


class DycHostWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DyCollector · 抖音采集下载器（开发中）")
        try:
            self.setWindowIcon(QIcon(str(config.ICON_PATH)))
        except Exception:
            pass
        self.resize(1280, 860)

        self.view = EngineView(self)
        central = QWidget(self)
        box = QVBoxLayout(central)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.view)
        self.setCentralWidget(central)

        tb = QToolBar("导航", self)
        tb.setMovable(False)
        self.addToolBar(tb)
        home = QAction("主页", self)
        home.triggered.connect(lambda: self._goto(config.HOME))
        fav = QAction("我的收藏", self)
        fav.triggered.connect(self._open_favorite)
        save = QAction("保存 Cookie 快照", self)
        save.triggered.connect(self._save_snapshot)
        tb.addAction(home)
        tb.addAction(fav)
        self._addr = QLineEdit(config.HOME, self)
        self._addr.returnPressed.connect(lambda: self._goto(self._addr.text().strip()))
        tb.addWidget(self._addr)
        go = QAction("前往", self)
        go.triggered.connect(lambda: self._goto(self._addr.text().strip()))
        tb.addAction(go)
        tb.addSeparator()
        tb.addAction(save)

        self.statusBar().addWidget(QLabel(f"数据目录：{config.DATA_DIR}"))
        self.statusBar().addPermanentWidget(QLabel(f"Cookie 持久化：{config.QT_PROFILE}"))

    # ---------- 导航 ----------
    def _goto(self, url: str):
        if url:
            self._addr.setText(url)
            self.view.open_url(url)

    def _open_favorite(self):
        self._goto(config.FAVORITE_URL)

    # ---------- 保存快照 ----------
    def _save_snapshot(self):
        def work():
            try:
                tab = self.view.attach()
                ck = cookies_mod.CookieKeeper()
                snap = cookies_mod.extract_from_tab(tab)
                ck.save(snap)
                ok = cookies_mod.has_login(snap)
                msg = (f"已保存 {len(snap)} 条 Cookie 到：\n{ck.path}\n"
                       + ("检测到登录态 ✓" if ok else "当前未见登录态（sessionid/sid_guard 缺失）"))
            except Exception as e:  # noqa: BLE001
                msg = f"保存失败：{e}"
            self._popup(msg)

        threading.Thread(target=work, daemon=True).start()

    def _popup(self, msg: str):
        try:
            QMessageBox.information(self, "Cookie 快照", msg)
        except Exception:
            pass

    def activate(self):
        """单实例唤醒：置顶并显示。"""
        self.show()
        self.raise_()
        self.activateWindow()
