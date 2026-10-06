"""检查 XHS __INITIAL_STATE__.user 的 loggedIn 和 userInfo 字段。"""
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

    js = r"""
    (function(){
        var s = window.__INITIAL_STATE__;
        if (!s || !s.user) return JSON.stringify({error: 'no user state'});
        var u = s.user;
        // 只提取基本类型，避免循环引用
        var safe = {};
        for (var k in u) {
            if (!u.hasOwnProperty(k)) continue;
            var v = u[k];
            var t = typeof v;
            if (t === 'boolean' || t === 'string' || t === 'number') {
                safe[k] = v;
            } else if (v === null) {
                safe[k] = null;
            } else if (t === 'object') {
                // 二级对象只取基本类型字段
                var sub = {};
                for (var k2 in v) {
                    if (!v.hasOwnProperty(k2)) continue;
                    var v2 = v[k2];
                    var t2 = typeof v2;
                    if (t2 === 'boolean' || t2 === 'string' || t2 === 'number' || v2 === null) {
                        sub[k2] = v2;
                    }
                }
                safe[k] = sub;
            }
        }
        return JSON.stringify(safe);
    })()
    """
    result = page.run_js(js, as_expr=True)
    print(f"[state] {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
