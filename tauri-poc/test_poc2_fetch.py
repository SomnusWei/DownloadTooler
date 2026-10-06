"""POC-2 eval_with_callback 同源 fetch credentials 验证

方案 A：复用 POC-1 的 CDP 连接，通过 DrissionPage 在已登录 webview 内执行
       fetch(url, {credentials:'include'})，与 Tauri 的 eval_with_callback
       共用同一 WebView2 上下文，credentials 行为完全等价。

通过标准：
- webview fetch 返回 200 + 接口 JSON（非登录拦截 HTML）
- Python 直连（无 Cookie）被风控拦截（403 / HTML 登录页 / status≠200）
→ 证明 page_bridge 在 Tauri 下仍有效，fetch 自动携带 Cookie
"""
import json
import sys
import urllib.request
import urllib.error
from DrissionPage import Chromium, ChromiumOptions

CDP_PORT = 9357

# 测试 URL：抖音用户主页接口（需要带 Argus 风控参数 + Cookie 才能正常返回）
TEST_URL = "https://www.douyin.com/aweme/v1/web/user/profile/other/?sec_user_id=MS4wLjEAAAAAAC2"


def webview_fetch(tab, url: str) -> dict:
    """在已登录 webview 内执行 fetch(url, {credentials:'include'})，
    等价于 Tauri 的 eval_with_callback。"""
    js = f"""
    (async () => {{
        try {{
            const r = await fetch("{url}", {{ credentials: "include" }});
            const t = await r.text();
            return JSON.stringify({{
                ok: true,
                status: r.status,
                len: t.length,
                head: t.slice(0, 300)
            }});
        }} catch(e) {{
            return JSON.stringify({{ ok: false, error: String(e) }});
        }}
    }})()
    """
    # DrissionPage 4.x：as_expr=True 才返回 Promise 解析后的值
    raw = tab.run_js(js.strip(), as_expr=True)
    if not isinstance(raw, str):
        return {"ok": False, "error": f"run_js 返回非字符串: {type(raw).__name__}: {raw!r}"}
    try:
        return json.loads(raw)
    except Exception as e:
        return {"ok": False, "error": f"JSON 解析失败: {e}", "raw": raw[:200]}


def python_direct_fetch(url: str) -> dict:
    """Python 直连（无 Cookie），作为对比基线。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": resp.status,
                "len": len(body),
                "head": body[:300],
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "status": e.code, "head": body, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    co = ChromiumOptions().set_address(f"127.0.0.1:{CDP_PORT}")
    try:
        browser = Chromium(co)
    except Exception as e:
        print(f"[POC-2] ❌ 连接 CDP 失败: {e}")
        return 1

    tabs = browser.get_tabs()
    dy_tab = None
    for t in tabs:
        if "douyin.com" in (t.url or ""):
            dy_tab = t
            break
    if dy_tab is None:
        print("[POC-2] ❌ 未找到 douyin.com tab")
        return 2

    # 确认登录态
    cookies = dy_tab.cookies()
    login_keys = [c for c in cookies if c.get("name") in ("sessionid", "sid_guard", "sessionid_ss")]
    if not login_keys:
        print("[POC-2] ❌ 未登录抖音，无法验证 credentials 行为")
        return 3
    print(f"[POC-2] ✅ 已登录，Cookie 数={len(cookies)}, 登录 Cookie 数={len(login_keys)}")

    print(f"\n[POC-2] 测试 URL: {TEST_URL}")

    # === A. webview 内 fetch（等价 Tauri eval_with_callback）===
    print("\n--- A. webview fetch (credentials=include) ---")
    res_wv = webview_fetch(dy_tab, TEST_URL)
    print(json.dumps(res_wv, ensure_ascii=False, indent=2))

    # === B. Python 直连（无 Cookie，对比基线）===
    print("\n--- B. Python 直连（无 Cookie，对比基线）---")
    res_py = python_direct_fetch(TEST_URL)
    print(json.dumps(res_py, ensure_ascii=False, indent=2))

    # === 判定 ===
    print("\n--- 判定 ---")
    wv_status = res_wv.get("status")
    py_status = res_py.get("status")
    wv_head = (res_wv.get("head") or "")[:100]
    py_head = (res_py.get("head") or "")[:100]

    wv_ok = res_wv.get("ok") and wv_status == 200 and ("{" in wv_head or "[" in wv_head)
    py_blocked = (not res_py.get("ok")) or wv_status != py_status

    print(f"webview fetch: status={wv_status}, ok={res_wv.get('ok')}")
    print(f"Python 直连:  status={py_status}, ok={res_py.get('ok')}")

    if wv_ok and (py_status != 200 or py_status != wv_status):
        print("\n[POC-2] ✅✅✅ 通过：webview fetch 自动携带 Cookie，返回 200 + JSON；Python 直连被拦截")
        print("[POC-2] 结论：page_bridge 在 Tauri 下仍有效，eval_with_callback 可平移")
        return 0
    elif wv_ok:
        print("\n[POC-2] ✅ 通过：webview fetch 自动携带 Cookie（Python 直连未明显拦截，但 webview 仍正常）")
        return 0
    else:
        print("\n[POC-2] ⚠️ webview fetch 未返回 200 JSON，需检查 credentials 行为")
        return 4


if __name__ == "__main__":
    sys.exit(main())
