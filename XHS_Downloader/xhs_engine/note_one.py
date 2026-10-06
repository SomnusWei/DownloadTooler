# -*- coding: utf-8 -*-
"""XHSCollector 单篇笔记解析 sidecar —— 右键/「下载笔记」共用

用法: python -u xhs_engine/note_one.py [--href <卡片链接|留空=当前页>]
逻辑: 挂接 Electron CDP(9361) → 复用 service.single_note_from_current:
  主路径: 用页面 Cookie HTTP 重取笔记页 SSR 解析作者/标题/类型(不打扰浏览);
  兜底:   让左侧视图打开一次直链再读 SSR。
输出: 过程日志;结束 ==XHS_JSON== + JSON {ok, meta, note, err}
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xhs_app import service  # noqa: E402

# 强制子进程 UTF-8（打包后默认 GBK 会中文乱码 / 编码崩溃）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PORT = 9361


def out(m): print(m, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--href", default="", help="笔记卡片链接；留空=取左侧当前页")
    args = ap.parse_args()

    from DrissionPage import Chromium, ChromiumOptions
    co = (ChromiumOptions().set_address(f"127.0.0.1:{PORT}")
          .set_timeouts(base=10, page_load=30, script=10)
          .set_retry(times=1, interval=0.2))
    tab = None
    for _ in range(6):
        try:
            browser = Chromium(co)                       # 浏览器级连接，不自动挂页
            hits = browser.get_tabs(url="xiaohongshu.com")
            if hits:
                tab = hits[0]
                try:
                    browser.activate_tab(tab)
                except Exception:
                    pass
                if "xiaohongshu.com" in (tab.url or ""):
                    break
                tab = None
        except Exception as e:
            out(f"[one] 挂接重试: {type(e).__name__} {str(e)[:80]}")
        time.sleep(1.0)
    if tab is None:
        out("[one] 无法连上小红书页面，请确认左侧浏览器已打开小红书后重试")
        print(json.dumps({"ok": False, "err": "no-xhs-tab"}, ensure_ascii=False)); return 3

    href = args.href.strip()
    try:
        meta, note = service.single_note_from_current(tab, hint_url=href)
    except Exception as e:
        out(f"[one] 解析失败: {type(e).__name__}: {e}")
        print(json.dumps({"ok": False, "err": str(e)[:200]}, ensure_ascii=False)); return 4

    out(f"[one] 已解析: {meta.nickname or meta.red_id}《{note.title or note.note_id}》 {note.kind}")
    payload = {
        "ok": True,
        "meta": {k: meta.__dict__.get(k) for k in
                 ("user_id", "href", "kw", "nickname", "red_id", "note_total", "source")},
        "note": {k: note.__dict__.get(k) for k in
                 ("note_id", "kind", "title", "cover_url", "liked", "xsec_token", "ts", "author")},
    }
    print("\n==XHS_JSON==\n" + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
