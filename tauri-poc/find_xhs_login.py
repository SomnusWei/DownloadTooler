"""尝试多种 XHS 登录 URL，找到能触发 QR 的页面。"""
import time
import sys
from DrissionPage import Chromium, ChromiumOptions

CDP_ADDR = "127.0.0.1:9361"

# 候选登录 URL
LOGIN_URLS = [
    "https://www.xiaohongshu.com/login",
    "https://www.xiaohongshu.com/login?redirect=https%3A%2F%2Fwww.xiaohongshu.com%2Fexplore",
    "https://www.xiaohongshu.com/explore",
    "https://www.xiaohongshu.com/",
]


def check_qr(page) -> dict:
    js = r"""
    (function(){
        // 全面扫描 QR 和登录元素
        var imgs = document.querySelectorAll('img, canvas');
        var qrImgs = [];
        for (var i = 0; i < imgs.length; i++) {
            var el = imgs[i];
            var cls = (el.className || '').toLowerCase();
            var src = (el.src || el.getAttribute('src') || '').toLowerCase();
            var alt = (el.alt || '').toLowerCase();
            if (cls.indexOf('qr') >= 0 || src.indexOf('qr') >= 0 || alt.indexOf('qr') >= 0 ||
                cls.indexOf('code') >= 0 || src.indexOf('code') >= 0) {
                qrImgs.push({tag: el.tagName, className: cls.slice(0,80), src: src.slice(0,120), alt: alt.slice(0,40)});
            }
        }
        // 找"扫码登录"文本
        var all = document.querySelectorAll('*');
        var scanTexts = [];
        for (var i = 0; i < all.length && i < 8000; i++) {
            var el = all[i];
            var txt = (el.textContent || '').trim();
            if ((txt === '扫码登录' || txt === '微信扫码' || txt === 'QQ登录' || txt === '手机扫码登录') && el.children.length === 0) {
                scanTexts.push({tag: el.tagName, text: txt, className: (el.className||'').slice(0,80)});
            }
        }
        return JSON.stringify({
            url: location.href,
            title: document.title,
            qrImgs: qrImgs.slice(0, 5),
            scanTexts: scanTexts.slice(0, 5),
            hasLoginText: (document.body.innerText || '').indexOf('登录') >= 0,
            bodyHead: (document.body.innerText || '').slice(0, 300)
        });
    })()
    """
    result = page.run_js(js, as_expr=True)
    if isinstance(result, str):
        import json
        try:
            return json.loads(result)
        except:
            return {"raw": result}
    return {"raw": result}


def main():
    co = ChromiumOptions().set_address(CDP_ADDR)
    browser = Chromium(co)
    page = None
    for t in browser.get_tabs():
        if "xiaohongshu.com" in (t.url or ""):
            page = t
            break
    if not page:
        print("[find] 未找到 xiaohongshu.com tab")
        return 1

    for url in LOGIN_URLS:
        print(f"\n[find] === 尝试 {url} ===")
        page.get(url)
        time.sleep(3)
        print(f"[find] 导航后 url: {page.url}")
        state = check_qr(page)
        print(f"[find] QR 数: {len(state.get('qrImgs', []))}")
        print(f"[find] 扫码文本数: {len(state.get('scanTexts', []))}")
        if state.get('qrImgs') or state.get('scanTexts'):
            print(f"[find] ✅ 找到登录元素！")
            print(f"[find] 详情: {state}")
            return 0
        else:
            print(f"[find] body 前 200: {state.get('bodyHead', '')[:200]}")

    print("\n[find] 所有 URL 都未触发 QR。请手动检查 xhs_main 窗口。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
