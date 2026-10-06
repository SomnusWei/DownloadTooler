# -*- coding: utf-8 -*-
"""临时诊断 v2:程序化点击顶部左侧图标,判断事件层/处理层"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dy_app import config


def make_js(x, y):
    return r"""return (function(){
  var x = %d, y = %d;
  var t = document.elementFromPoint(x, y);
  var el = (t && t.closest) ? t.closest('a,button,[role="button"],[data-e2e]') : null;
  if (!el) el = t;
  if (!el) return 'no-el';
  if (typeof el.click === 'function') {
    try { el.click(); return 'clicked:' + el.tagName + ':' + String(el.className||'').slice(0,24); }
    catch (e) { return 'err:' + e.message; }
  }
  return 'nofn:' + el.tagName;
})()""" % (x, y)


def main() -> int:
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions().set_address(f"127.0.0.1:{config.QT_CDP_PORT}")
    tab = None
    for _ in range(20):
        try:
            tab = ChromiumPage(co)
            break
        except Exception:
            time.sleep(0.5)
    if tab is None:
        print("attach fail")
        return 1

    def href():
        try:
            return tab.run_js("return location.href")
        except Exception as e:
            return f"<err {type(e).__name__}>"

    print("before =", href())
    for x in (24, 60, 120, 200):
        try:
            r = tab.run_js(make_js(x, 26))
        except Exception as e:
            r = f"<jserr {type(e).__name__}>"
        print(f"click @({x},26) ->", r)
        time.sleep(1.5)
        print("   after =", href())
    return 0


if __name__ == "__main__":
    sys.exit(main())
