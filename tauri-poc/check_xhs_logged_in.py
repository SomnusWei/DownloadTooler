"""检查 XHS 当前实际登录态：CDP cookies + 页面 DOM 用户元素 + fetch selfinfo API。"""
import json
import sys
import requests
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
        print("[check] 未找到 tab")
        return 1

    print(f"[check] url: {page.url}")

    # 1. 通过 CDP 拿 cookies（含 HttpOnly）
    r = requests.get(f"http://{CDP_ADDR}/json", timeout=5)
    targets = r.json()
    page_target = next((t for t in targets if t.get("type") == "page" and "xiaohongshu.com" in t.get("url", "")), None)
    if page_target:
        ws_url = page_target["webSocketDebuggerUrl"]
        import websocket
        ws = websocket.create_connection(ws_url, timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        result = json.loads(ws.recv())
        ws.close()
        cookies = result.get("result", {}).get("cookies", [])
        xhs_cookies = [c for c in cookies if "xiaohongshu.com" in c.get("domain", "")]
        web_session = next((c for c in xhs_cookies if c["name"] == "web_session"), None)
        print(f"[check] cookies 数: {len(xhs_cookies)}")
        print(f"[check] web_session: {web_session['value'][:40] if web_session else '不存在'}...")

    # 2. 通过 fetch selfinfo API 检查（带 credentials）
    print("\n[check] === fetch selfinfo API ===")
    js_fetch = r"""
    (async function(){
        try {
            var r = await fetch('https://edith.xiaohongshu.com/api/sns/web/v1/user/selfinfo', {credentials:'include'});
            var txt = '';
            try { txt = (await r.text()).slice(0, 400); } catch(_){}
            return JSON.stringify({status: r.status, ok: r.ok, body: txt});
        } catch(e) {
            return JSON.stringify({error: String(e)});
        }
    })()
    """
    result = page.run_js(js_fetch, as_expr=True)
    print(f"[check] selfinfo: {result}")

    # 3. 重新加载页面，刷新 __INITIAL_STATE__
    print("\n[check] === 重新加载页面刷新 __INITIAL_STATE__ ===")
    page.get("https://www.xiaohongshu.com/explore")
    import time
    time.sleep(4)
    js_state = r"""
    (function(){
        try {
            var s = window.__INITIAL_STATE__;
            return JSON.stringify({
                logged_in: !!(s && s.user && s.user.selfInfo && s.user.selfInfo.id),
                user_id: s && s.user && s.user.selfInfo && s.user.selfInfo.id,
                user_keys: s && s.user ? Object.keys(s.user).slice(0, 10) : null,
                url: location.href
            });
        } catch(e) {
            return JSON.stringify({error: String(e)});
        }
    })()
    """
    result2 = page.run_js(js_state, as_expr=True)
    print(f"[check] 刷新后: {result2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
