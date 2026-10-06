# -*- coding: utf-8 -*-
"""XHSCollector 列表采集 sidecar —— 挂接 Electron CDP 收割作者主页作品列表

用法: python -u xhs_engine/collect_one.py [--raw <主页链接|小红书号|空=抓当前>]
输出: 过程日志;结束打印 ==XHS_JSON== + JSON {ok, meta, notes}
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xhs_app import collector, config, models, resolver, service  # noqa: E402

# 强制子进程 UTF-8（打包后默认 GBK 会中文乱码 / 编码崩溃）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PORT = 9361
ENV_ROOT = Path(os.environ.get("XHS_ROOT") or ROOT)   # 打包版：cookies 与主程序共用目录


def out(m): print(m, flush=True)


def export_cookies(dp) -> str:
    rows = []
    for c in dp.cookies():
        if not isinstance(c, dict):
            continue
        dom = str(c.get("domain") or "")
        if "xiaohongshu.com" in dom and c.get("name") and c.get("value"):
            rows.append({"name": c["name"], "value": c["value"], "domain": dom})
    header = "; ".join(f"{r['name']}={r['value']}" for r in rows)
    (ENV_ROOT / "cookies.json").write_text(json.dumps(
        {"saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "cookie_header": header, "cookies": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return header


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="", help="小红书号 / 主页链接；留空=抓 Electron 当前页")
    args = ap.parse_args()

    import time as _time
    from DrissionPage import Chromium, ChromiumOptions

    # 关键：ChromiumPage() 会自动挂接 CDP 的"第一个" page target，
    # Electron 下该目标可能是面板/空白宿主页，忙碌时 Page.getFrameTree 会长时间超时。
    # 这里只做浏览器级连接（Chromium，不自动挂任何页），再显式按 URL 选中小红书视图，
    # 与 Dy 原版 get_tabs(url='douyin.com') 同一思路。
    co = (ChromiumOptions().set_address(f"127.0.0.1:{PORT}")
          .set_timeouts(base=10, page_load=30, script=10)
          .set_retry(times=1, interval=0.2))
    tab = None
    last_err = ""
    for _attempt in range(8):
        try:
            browser = Chromium(co)                      # 仅浏览器端点，不做页级挂接
            hits = browser.get_tabs(url="xiaohongshu.com")
            if hits:
                tab = hits[0]
                try:
                    browser.activate_tab(tab)
                except Exception:
                    pass
                _u = tab.url or ""                      # 触发一次页级挂接验证可响应
                if "xiaohongshu.com" in _u:
                    break
                tab = None
        except Exception as e:
            last_err = f"{type(e).__name__} {str(e)[:80]}"
            out(f"[xh] 挂接重试 {_attempt + 1}: {last_err}")
        _time.sleep(1.0)
    if tab is None:
        out(f"[xh] 无法连上小红书页面（CDP 目标选择失败: {last_err or '未找到 xiaohongshu 标签'}），请确认左侧浏览器已打开小红书后重试")
        print(json.dumps({"ok": False, "err": "no-xhs-tab"}, ensure_ascii=False)); return 3
    chromium = tab

    # 必须在任何导航前开启 user_posted 监听（先监听→再导航），
    # 否则拿不到 XHR 分页链，只会解析 SSR 首屏那一截 → 数量不全。
    try:
        tab.listen.start(config.USER_POSTED)
    except Exception as e:
        out(f"[xh] 启动 user_posted 监听失败(将退化为页面渲染解析): {type(e).__name__}: {str(e)[:90]}")

    raw = args.raw.strip()
    try:
        if raw:
            meta = resolver.resolve(tab, raw)         # 小红书号/主页链接 → ProfileMeta
        else:
            meta = service.meta_from_current(tab)     # 抓当前页(须作者主页)
    except Exception as e:
        out(f"[xh] 解析目标失败: {type(e).__name__}: {e}")
        print(json.dumps({"ok": False, "err": str(e)[:160]}, ensure_ascii=False)); return 4

    if not (meta.href or meta.user_id):
        out("[xh] 该输入不是可采集的作者主页")
        print(json.dumps({"ok": False, "err": "not-profile"}, ensure_ascii=False)); return 4
    out(f"[xh] 目标作者: {meta.nickname or meta.user_id} · {meta.red_id or '-'}")
    try:
        if meta.href:
            out("[xh] 打开作者主页…")
            tab.get(meta.href)
            _time.sleep(1.2)                          # 等首屏 user_posted 入队
        res = collector.collect(tab, meta, log=out)
        collector.fill_meta_from_dom(tab, meta)
    except Exception as e:
        out(f"[xh] 采集失败: {type(e).__name__}: {e}")
        print(json.dumps({"ok": False, "err": str(e)[:160]}, ensure_ascii=False)); return 5

    export_cookies(tab)
    notes = [{k: (v if not isinstance(v, (dict, list)) else None) for k, v in n.__dict__.items()}
             for n in res.notes]
    payload = {"ok": True, "meta": meta.__dict__,
               "notes": [{"note_id": n.note_id, "kind": n.kind, "title": n.title,
                          "cover_url": n.cover_url, "liked": n.liked,
                          "xsec_token": n.xsec_token, "ts": n.ts, "author": n.author}
                         for n in res.notes],
               "count": len(res.notes), "stopped": res.stopped}
    out(f"[xh] 完成: {len(res.notes)} 篇（{res.stopped}）")
    print("\n==XHS_JSON==\n" + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
