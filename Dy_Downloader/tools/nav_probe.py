# -*- coding: utf-8 -*-
"""临时诊断:采样 Electron 当前页顶部导航 DOM,定位“回首页/后退”按钮实现与可点击性"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dy_app import config

JS = r"""
(function () {
  var out = { url: location.href, top: [], probes: [] };
  // 顶部可视区域里的可点击元素(前 40)
  var els = document.querySelectorAll('a,button,[role="button"],[data-click],[data-e2e]');
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    var r;
    try { r = el.getBoundingClientRect(); } catch (e) { continue; }
    if (r.width < 4 || r.height < 4) continue;
    if (r.top > 80) continue;
    var txt = (el.innerText || '').trim().slice(0, 14);
    out.top.push({
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0, 50),
      txt: txt,
      href: el.getAttribute && el.getAttribute('href'),
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]
    });
    if (out.top.length >= 40) break;
  }
  // 左上采样点实际命中的元素链(验证是否被遮挡)
  [[30, 30], [80, 30], [150, 30], [220, 30]].forEach(function (p) {
    var hit = document.elementsFromPoint(p[0], p[1]);
    out.probes.push({ pt: p, hits: hit.slice(0, 3).map(function (h) {
      var st = window.getComputedStyle(h);
      return {
        tag: h.tagName, txt: (h.innerText || '').trim().slice(0, 8),
        cls: (h.className || '').toString().slice(0, 30),
        pe: st.pointerEvents, z: st.zIndex, tag2: h.tagName
      };
    }) });
  });
  return JSON.stringify(out);
})()
"""


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
    print("URL:", tab.url)
    raw = tab.run_js(JS)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        data = raw
    print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
