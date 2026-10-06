"""点击 XHS 页面"我"按钮，触发登录弹窗。"""
import time
import sys
import json
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
        print("[trigger] 未找到 xiaohongshu.com tab")
        return 1
    print(f"[trigger] 当前 url: {page.url}")
    # 尝试点击"我"按钮
    click_js = r"""
    (function(){
        // 找所有可能的"我"按钮
        var candidates = [];
        var all = document.querySelectorAll('a, button, span, div');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var txt = (el.textContent || '').trim();
            if (txt === '我' && el.children.length === 0) {
                candidates.push({tag: el.tagName, text: txt, className: (el.className||'').slice(0,80), id: el.id, href: el.href||null});
            }
        }
        // 点击第一个"我"
        if (candidates.length > 0) {
            var target = all;
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                if ((el.textContent || '').trim() === '我' && el.children.length === 0) {
                    el.click();
                    return JSON.stringify({clicked: true, target: candidates[0]});
                }
            }
        }
        return JSON.stringify({clicked: false, candidates: candidates.slice(0, 5)});
    })()
    """
    result = page.run_js(click_js, as_expr=True)
    print(f"[trigger] 点击结果: {result}")
    time.sleep(3)
    print(f"[trigger] 点击后 url: {page.url}")
    # 检查登录弹窗
    check_js = r"""
    (function(){
        var qr = document.querySelector('[class*="qrcode" i], [class*="qrCode" i], [id*="qr" i] img, .qrcode, canvas');
        var dialog = document.querySelector('[class*="dialog" i][style*="display: block"], [class*="modal" i][style*="display: block"], [class*="login" i][class*="container" i]');
        // 检查所有 img 元素（QR 通常是 img 或 canvas）
        var imgs = document.querySelectorAll('img, canvas');
        var qrImgs = [];
        for (var i = 0; i < imgs.length; i++) {
            var img = imgs[i];
            var cls = (img.className || '').toLowerCase();
            var src = img.src || img.getAttribute('src') || '';
            if (cls.indexOf('qr') >= 0 || src.indexOf('qr') >= 0 || cls.indexOf('code') >= 0) {
                qrImgs.push({tag: img.tagName, className: cls.slice(0,80), src: src.slice(0,100)});
            }
        }
        return JSON.stringify({
            url: location.href,
            qrImgs: qrImgs,
            hasDialog: !!dialog,
            dialogClass: dialog ? (dialog.className||'').slice(0,80) : null,
            bodyTextHead: (document.body.innerText || '').slice(0, 200)
        }, null, 2);
    })()
    """
    result2 = page.run_js(check_js, as_expr=True)
    print(f"[trigger] 状态: {result2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
