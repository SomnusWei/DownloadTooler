"""POC-1 DrissionPage 连接 Tauri WebView2 CDP 测试"""
import sys
from DrissionPage import Chromium, ChromiumOptions

CDP_PORT = 9357


def main():
    # 连接 Tauri WebView2 的 CDP 端口
    co = ChromiumOptions().set_address(f"127.0.0.1:{CDP_PORT}")
    try:
        browser = Chromium(co)
    except Exception as e:
        print(f"[POC-1] ❌ 连接 CDP 失败: {e}")
        sys.exit(1)

    # 列出所有 tab
    tabs = browser.get_tabs()
    print(f"[POC-1] 找到 {len(tabs)} 个 tab:")
    for t in tabs:
        url = t.url or ""
        title = t.title or ""
        print(f"  - url={url[:60]}, title={title[:30]}")

    # 选择抖音 tab
    dy_tab = None
    for t in tabs:
        if "douyin.com" in (t.url or ""):
            dy_tab = t
            break

    if dy_tab is None:
        print("[POC-1] ❌ 未找到 douyin.com tab")
        sys.exit(2)

    print(f"[POC-1] ✅ 已选择抖音 tab: {(dy_tab.url or '')[:80]}")

    # 在页面内执行 JS（DrissionPage 4.x：as_expr=True 才返回值）
    try:
        result = dy_tab.run_js("document.title", as_expr=True)
        print(f"[POC-1] 页面 title: {result}")
    except Exception as e:
        print(f"[POC-1] ❌ run_js 失败: {e}")
        sys.exit(3)

    # Cookie 检测
    cookies = dy_tab.cookies()
    print(f"[POC-1] Cookie 数量: {len(cookies)}")
    login_keys = [c for c in cookies if c.get('name') in ('sessionid', 'sid_guard', 'sessionid_ss')]
    print(f"[POC-1] 登录 Cookie: {'已登录' if login_keys else '未登录（不影响 POC）'}")

    print("[POC-1] ✅✅✅ 通过：DrissionPage 可连接 WebView2 CDP 并操作页面")
    return 0


if __name__ == "__main__":
    sys.exit(main())
