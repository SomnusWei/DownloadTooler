# -*- coding: utf-8 -*-
"""XHSCollector 单篇下载 sidecar —— 纯 HTTP + 本地 cookies.json（不挂浏览器）

用法: python -u xhs_engine/download_one.py --note-json <file> --dir <目标目录>
  note-json: {"note": {...NoteItem 字段}, "meta": {...ProfileMeta 字段}}
输出: 过程日志;结束 ==XHS_JSON== + JSON {ok, state, msg}
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xhs_app import models, queue, service  # noqa: E402
from xhs_app.models import LoginRequired  # noqa: E402

# 强制子进程 UTF-8（打包后默认 GBK 会中文乱码 / 编码崩溃）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ENV_ROOT = Path(os.environ.get("XHS_ROOT") or ROOT)   # 打包版：cookies 与主程序共用目录


def out(m): print(m, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--note-json", required=True)
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    try:
        cfg = json.loads(Path(args.note_json).read_text(encoding="utf-8"))
        meta = models.ProfileMeta(**{k: cfg["meta"].get(k) for k in
                                     ("user_id", "href", "kw", "nickname", "red_id",
                                      "note_total", "source")})
        n = cfg["note"]
        note = models.NoteItem(**{k: n.get(k) for k in
                                  ("note_id", "kind", "title", "cover_url", "liked",
                                   "xsec_token", "ts", "author", "status")})
    except Exception as e:
        out(f"[dl] note-json 解析失败: {e}")
        print(json.dumps({"ok": False, "err": "bad-note-json"}, ensure_ascii=False)); return 2

    cj = ENV_ROOT / "cookies.json"
    try:
        header = json.loads(cj.read_text(encoding="utf-8")).get("cookie_header") or ""
    except Exception:
        header = ""
    if not header:
        out("[dl] 本地 cookies.json 缺失或为空，请先抓取一次（自动生成）或检查 Electron 登录")
        print(json.dumps({"ok": False, "err": "no-cookie"}, ensure_ascii=False)); return 3

    out(f"[dl] {note.note_id} · {note.kind} · {note.title[:24]}")
    task = queue.TaskItem(note=note, meta=meta, target_dir=str(Path(args.dir)))
    try:
        state, msg = service.run_download_task(task, header, log=out)
    except LoginRequired as e:
        out(f"[dl] 登录失效：{e}")
        print("\n==XHS_JSON==\n" + json.dumps(
            {"ok": False, "state": "login_required", "msg": str(e)}, ensure_ascii=False), flush=True)
        return 7
    except Exception as e:
        out(f"[dl] 异常 {type(e).__name__}: {e}")
        print(json.dumps({"ok": False, "err": str(e)[:160]}, ensure_ascii=False)); return 4
    ok = state in (queue.DONE, queue.SKIPPED)
    out(f"[dl] {state} | {msg}")
    print("\n==XHS_JSON==\n" + json.dumps({"ok": ok, "state": state, "msg": msg}, ensure_ascii=False), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
