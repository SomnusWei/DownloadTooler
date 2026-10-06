# -*- coding: utf-8 -*-
"""XH0 spike —— 验证「Electron 内置浏览器 + Python sidecar 复用 XHS 业务层」

用法（在 XHS_Downloader 目录下）：
  python spike_xh0.py collect     # 先让 Electron 停在小红书作者主页；收割列表+存本地cookies
  python spike_xh0.py dl          # 用本地 cookies.json 纯 HTTP 下载列表首篇（不挂浏览器）
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from xhs_app import collector, config, models, queue, service  # noqa: E402


def out(m):
    print(m, flush=True)


def attach():
    from DrissionPage import ChromiumPage, ChromiumOptions
    co = ChromiumOptions().set_address("127.0.0.1:9360")
    tab = ChromiumPage(addr_or_opts=co)
    return tab


def export_cookies(dp) -> str:
    """本机 cookie → cookies.json（本地持久化；后续 dl 纯后台回放，不重复扫码）"""
    rows = []
    for c in dp.cookies():
        if not isinstance(c, dict):
            continue
        dom = str(c.get("domain") or "")
        if "xiaohongshu.com" in dom and c.get("name") and c.get("value"):
            rows.append({"name": c["name"], "value": c["value"], "domain": dom})
    header = "; ".join(f"{r['name']}={r['value']}" for r in rows)
    cfg = {"saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "cookie_header": header, "cookies": rows}
    (ROOT / "cookies.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    out(f"本地 cookies.json 已保存（{len(rows)} 条）")
    return header


def load_cookie_header() -> str:
    cfg = json.loads((ROOT / "cookies.json").read_text(encoding="utf-8"))
    return cfg.get("cookie_header") or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["collect", "dl"])
    args = ap.parse_args()

    if args.action == "collect":
        tab = attach()
        out(f"[xh0] CDP 已挂接: {tab.url}")
        try:
            meta = service.meta_from_current(tab)    # 要求当前为作者主页
        except Exception as e:
            out(f"[xh0] {type(e).__name__}: {e}")
            out("请先在 Electron 窗口打开一位作者的主页（头像/作品列表页），再运行 collect。")
            return 3
        out(f"[xh0] 作者: {meta.user_id} url={meta.href[:80] if meta.href else ''}")
        try:
            res = collector.collect(tab, meta, log=out)
            collector.fill_meta_from_dom(tab, meta)
        except Exception as e:
            out(f"[xh0] 采集失败 {type(e).__name__}: {e}")
            return 4
        out(f"[xh0] 收集 {len(res.notes)} 篇 | 有更多={res.stopped}")
        snapshot = {"meta": meta.__dict__,
                    "notes": [n.__dict__ for n in res.notes]}
        (ROOT / ".xhs_xh0_out.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        if res.notes:
            n = res.notes[0]
            out(f"[xh0] 样本: {n.note_id} {n.kind} {n.title[:24]} xsec={'有' if n.xsec_token else '无'}")
        export_cookies(tab)
        out("[xh0] collect OK")
        return 0

    # dl：用本地 cookies.json，不挂浏览器
    header = load_cookie_header()
    if not header:
        out("请先运行 collect 生成 cookies.json")
        return 2
    snap = json.loads((ROOT / ".xhs_xh0_out.json").read_text(encoding="utf-8"))
    meta = models.ProfileMeta(**{k: snap["meta"].get(k) for k in
                                 ("user_id", "href", "kw", "nickname", "red_id",
                                  "note_total", "source")})
    note_d = snap["notes"][0]
    note = models.NoteItem(**{k: note_d.get(k) for k in
                              ("note_id", "kind", "title", "cover_url", "liked",
                               "xsec_token", "ts", "author", "status")})
    target_dir = str(ROOT / ".xhs_xh0")
    task = queue.TaskItem(note=note, meta=meta, target_dir=target_dir)
    out(f"[xh0] 开始纯 HTTP 下载首篇: {note.note_id} → {target_dir}")
    state, msg = service.run_download_task(task, header, log=out)
    out(f"[xh0] 结果: {state} | {msg}")
    return 0 if state == queue.DONE or state == queue.SKIPPED else 1


if __name__ == "__main__":
    sys.exit(main())
