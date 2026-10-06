# -*- coding: utf-8 -*-
"""图集现状探针：低频跑 待处理.md 三个回归链接，打印 detail 字段与首图可达性"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dy_app import dy_fetch  # noqa: E402

cj = json.loads((ROOT / "cookies.json").read_text("utf-8"))
ck = cj.get("cookies") or cj
if isinstance(ck, list):
    ck = {c["name"]: c["value"] for c in ck if c.get("value")}
print("cookie keys:", sorted(ck.keys()))
print("has msToken:", bool((ck.get("msToken") or "").strip()))
print("msToken len:", len((ck.get("msToken") or "").strip()))

URLS = [
    "https://www.douyin.com/video/7680850290924851067",   # 图文 8 图
    "https://www.douyin.com/video/7680045761287513082",   # 图文 7 图
    "https://www.douyin.com/note/7420018490091851047",    # note 图集
]

for u in URLS:
    print("\n===== ", u)
    try:
        meta, medias = dy_fetch.fetch_detail(u, ck)
        print("meta:", json.dumps(meta, ensure_ascii=False))
        print("media count:", len(medias), "first:", (medias[0] if medias else None))
        # 探首图可达性（防盗链 Headers）
        if medias:
            import urllib.request
            req = urllib.request.Request(medias[0][0], headers={
                "User-Agent": dy_fetch.UA,
                "Referer": "https://www.douyin.com/",
                "Cookie": dy_fetch._cookie_header(ck),
            }, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    b = r.read(64)
                    print("img probe:", r.status, r.headers.get("Content-Type"), len(b), "bytes head")
            except Exception as e:
                print("img probe FAIL:", type(e).__name__, getattr(e, "code", ""), str(e)[:140])
    except Exception as e:
        print("FETCH FAIL:", type(e).__name__, getattr(e, "code", ""), str(e)[:200])
    time.sleep(5)   # 低频
