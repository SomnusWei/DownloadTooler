"""深度检查 XHS 页面登录入口元素。"""
import json
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
        print("[inspect] 未找到 xiaohongshu.com tab")
        return 1
    print(f"[inspect] url: {page.url}")
    js = r"""
    (function(){
        // 收集所有可能的登录入口元素
        var results = [];
        // 1. 文本含"登录"的元素
        var all = document.querySelectorAll('button, a, span, div');
        for (var i = 0; i < all.length && i < 5000; i++) {
            var el = all[i];
            var txt = (el.textContent || '').trim();
            if (txt === '登录' || txt === '登 录' || txt === '扫码登录' || txt === 'Log in' || txt === 'Login') {
                results.push({tag: el.tagName, text: txt.slice(0,20), className: (el.className||'').slice(0,80), id: el.id});
            }
        }
        // 2. 检查 iframe
        var iframes = document.querySelectorAll('iframe');
        var iframeInfo = [];
        for (var j = 0; j < iframes.length; j++) {
            iframeInfo.push({src: iframes[j].src, id: iframes[j].id});
        }
        // 3. 检查 body 前 500 字符
        var bodyText = (document.body.innerText || '').slice(0, 500);
        return JSON.stringify({
            loginMatches: results.slice(0, 10),
            iframeCount: iframes.length,
            iframes: iframeInfo,
            bodyTextHead: bodyText,
            url: location.href,
            title: document.title
        }, null, 2);
    })()
    """
    result = page.run_js(js, as_expr=True)
    print(f"[inspect] {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
