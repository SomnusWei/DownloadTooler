"""导航 xhs_main 到 explore 页面，触发登录 QR 弹窗。"""
import time
import sys
from DrissionPage import Chromium, ChromiumOptions

CDP_ADDR = "127.0.0.1:9361"


def main():
    co = ChromiumOptions().set_address(CDP_ADDR)
    browser = Chromium(co)
    page = None
    for t in browser.get_tabs():
        if "xiaohongshu.com" in (t.url or ""):
            page = t
            break
    if not page:
        print("[nav] 未找到 xiaohongshu.com tab")
        return 1
    print(f"[nav] 当前 url: {page.url}")
    # 导航到 explore，应该会触发登录弹窗
    page.get("https://www.xiaohongshu.com/explore")
    time.sleep(3)
    print(f"[nav] 导航后 url: {page.url}")
    # 检查 QR
    js = r"""
    (function(){
        var qr = document.querySelector('[class*="qr" i] img, [class*="QR" i] img, .code-container img, #qrcode, canvas.qrcode, [class*="qrcode" i]');
        var loginDialog = document.querySelector('[class*="login" i][class*="container" i], [class*="Login" i][class*="Container" i]');
        return JSON.stringify({
            hasQr: !!qr,
            qrTag: qr ? (qr.tagName + '.' + (qr.className||'').slice(0,50)) : null,
            hasLoginDialog: !!loginDialog,
            url: location.href
        });
    })()
    """
    result = page.run_js(js, as_expr=True)
    print(f"[nav] 状态: {result}")
    # 如果没有 QR，尝试点击登录按钮
    if not result or '"hasQr": true' not in str(result):
        print("[nav] 未发现 QR，尝试点击登录按钮...")
        click_js = r"""
        (function(){
            var btn = document.querySelector('[class*="login" i] button, [class*="Login" i] button, .login-btn, [data-e2e*="login" i]');
            if (btn) { btn.click(); return 'clicked:' + btn.className; }
            return 'no login button found';
        })()
        """
        click_result = page.run_js(click_js, as_expr=True)
        print(f"[nav] 点击结果: {click_result}")
        time.sleep(2)
        result2 = page.run_js(js, as_expr=True)
        print(f"[nav] 点击后状态: {result2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
