"""轮询 xhs_main (CDP 9361) 登录状态，等待用户扫码登录。
每 3 秒检查 window.__INITIAL_STATE__.user.selfInfo.id，
登录成功后立即导出 cookies.json 并退出。
"""
import json
import time
import sys
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions


CDP_ADDR = "127.0.0.1:9361"
COOKIE_FILE = Path(r"e:\item\DownloadTooler\XHS_Downloader\cookies.json")
POLL_INTERVAL = 3
MAX_WAIT = 180  # 3 分钟


def connect():
    co = ChromiumOptions().set_address(CDP_ADDR)
    # 不新开浏览器，连接已有 WebView2 实例
    return Chromium(co)


def check_login_state(page) -> dict:
    """返回 {logged_in, user_id, has_qr, url}"""
    js = r"""
    (function(){
        try {
            var s = window.__INITIAL_STATE__;
            var uid = s && s.user && s.user.selfInfo && s.user.selfInfo.id;
            // 检查页面是否有 QR 登录弹窗
            var qr = document.querySelector('[class*="qr" i] img, [class*="QR" i] img, .code-container img, #qrcode');
            return JSON.stringify({
                logged_in: !!uid,
                user_id: uid,
                has_qr: !!qr,
                url: location.href,
                ts: Date.now()
            });
        } catch(e) {
            return JSON.stringify({logged_in: false, error: String(e)});
        }
    })()
    """
    try:
        result = page.run_js(js, as_expr=True)
        if isinstance(result, str):
            return json.loads(result)
        return {"logged_in": False, "raw": result}
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


def export_cookies(page):
    """通过 page.cookies() 导出，写为 XHS_Downloader/cookies.json 格式"""
    cookies = page.cookies(all_domains=True)
    # 过滤 xiaohongshu.com 域
    xhs = [c for c in cookies if "xiaohongshu.com" in c.get("domain", "")]
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in xhs)
    rows = [{"domain": c.get("domain", ""), "name": c["name"], "value": c["value"]} for c in xhs]
    payload = {
        "cookie_header": cookie_header,
        "cookies": rows,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(xhs)


def main():
    print(f"[poll] 连接 CDP {CDP_ADDR} ...")
    try:
        browser = connect()
    except Exception as e:
        print(f"[poll] 连接失败: {e}")
        sys.exit(1)

    tabs = browser.get_tabs()
    print(f"[poll] 找到 {len(tabs)} 个 tab")
    for t in tabs:
        print(f"  - url={t.url}")

    # 找 xiaohongshu.com 的 tab
    page = None
    for t in tabs:
        if "xiaohongshu.com" in (t.url or ""):
            page = t
            break
    if not page:
        print("[poll] 未找到 xiaohongshu.com tab")
        sys.exit(1)

    print(f"[poll] 监控 page: {page.url}")
    print(f"[poll] 请用小红书 APP 扫描 xhs_main 窗口中的二维码")
    print(f"[poll] 轮询间隔 {POLL_INTERVAL}s，最长等待 {MAX_WAIT}s")

    start = time.time()
    last_state = None
    while time.time() - start < MAX_WAIT:
        state = check_login_state(page)
        if state != last_state:
            elapsed = int(time.time() - start)
            print(f"[poll] {elapsed}s 状态: {state}")
            last_state = state
        if state.get("logged_in"):
            print(f"[poll] ✅ 登录成功！user_id={state.get('user_id')}")
            # 等待 2 秒让 cookie 完全写入
            time.sleep(2)
            n = export_cookies(page)
            print(f"[poll] ✅ 已导出 {n} 条 cookies 到 {COOKIE_FILE}")
            return 0
        time.sleep(POLL_INTERVAL)

    print("[poll] ⏰ 超时未登录")
    return 2


if __name__ == "__main__":
    sys.exit(main())
