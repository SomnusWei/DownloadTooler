"""清除 XHS WebView2 的 cookies，重新加载页面，触发登录 QR。"""
import time
import sys
import json
import requests
from DrissionPage import Chromium, ChromiumOptions

CDP_ADDR = "127.0.0.1:9361"


def cdp_send(ws_url: str, method: str, params: dict = None):
    """通过 CDP WebSocket 发送命令。"""
    import websocket
    ws = websocket.create_connection(ws_url, timeout=10)
    msg = {"id": 1, "method": method}
    if params:
        msg["params"] = params
    ws.send(json.dumps(msg))
    result = ws.recv()
    ws.close()
    return json.loads(result)


def main():
    # 拿 page target ws url
    r = requests.get(f"http://{CDP_ADDR}/json", timeout=5)
    targets = r.json()
    page_target = next((t for t in targets if t.get("type") == "page" and "xiaohongshu.com" in t.get("url", "")), None)
    if not page_target:
        print("[clear] 未找到 xhs page target")
        return 1
    ws_url = page_target["webSocketDebuggerUrl"]
    print(f"[clear] target: {page_target['url']}")
    print(f"[clear] ws: {ws_url}")

    # 用 DrissionPage 操作
    co = ChromiumOptions().set_address(CDP_ADDR)
    browser = Chromium(co)
    page = None
    for t in browser.get_tabs():
        if "xiaohongshu.com" in (t.url or ""):
            page = t
            break
    if not page:
        print("[clear] DrissionPage 未找到 tab")
        return 1

    # 1. CDP 清除所有 cookies
    try:
        import websocket
        ws = websocket.create_connection(ws_url, timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Network.clearBrowserCookies"}))
        result = ws.recv()
        print(f"[clear] clearBrowserCookies: {result[:200]}")
        ws.close()
    except Exception as e:
        print(f"[clear] clearBrowserCookies 失败: {e}")

    # 2. 重新加载页面
    print("[clear] 重新加载 /explore ...")
    page.get("https://www.xiaohongshu.com/explore")
    time.sleep(4)
    print(f"[clear] 当前 url: {page.url}")

    # 3. 检查 QR
    js = r"""
    (function(){
        // 真正的 QR 通常是 canvas 或 img 在登录弹窗里
        var canvas = document.querySelector('canvas');
        var loginContainer = document.querySelector('[class*="login-container" i], [class*="loginContainer" i], [class*="qrcode-container" i]');
        // 检查所有可见的弹窗
        var dialogs = document.querySelectorAll('[class*="dialog" i], [class*="modal" i], [class*="Dialog" i], [class*="Modal" i]');
        var visibleDialogs = [];
        for (var i = 0; i < dialogs.length; i++) {
            var d = dialogs[i];
            var rect = d.getBoundingClientRect();
            if (rect.width > 50 && rect.height > 50) {
                visibleDialogs.push({className: (d.className||'').slice(0,100), w: rect.width, h: rect.height});
            }
        }
        return JSON.stringify({
            url: location.href,
            hasCanvas: !!canvas,
            canvasClass: canvas ? (canvas.className||'').slice(0,80) : null,
            hasLoginContainer: !!loginContainer,
            loginContainerClass: loginContainer ? (loginContainer.className||'').slice(0,80) : null,
            visibleDialogs: visibleDialogs.slice(0, 5),
            bodyTextHead: (document.body.innerText || '').slice(0, 300)
        }, null, 2);
    })()
    """
    result = page.run_js(js, as_expr=True)
    print(f"[clear] 状态: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
