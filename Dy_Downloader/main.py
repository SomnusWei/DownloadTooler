# -*- coding: utf-8 -*-
"""Dy_Downloader 入口：内嵌浏览器宿主 + 进程单实例（重复双击只唤醒已有窗口）

M1 起将接入完整主界面（dy_app/ui.py），此处保留过渡宿主便于先行验证登录与 Cookie 持久化。
"""
import sys

from PySide6.QtWidgets import QApplication

from dy_app.host import DycHostWindow
from dy_app.single import SingleInstance


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DyCollector")
    guard = SingleInstance()
    if not guard.become_primary():
        # 已有实例在运行：唤醒它，本进程退出
        guard.request_activate()
        return 0
    win = DycHostWindow()
    guard.on_activate = win.activate
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
