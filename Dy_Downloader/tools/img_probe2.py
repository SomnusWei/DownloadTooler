# -*- coding: utf-8 -*-
"""msToken / 参数变体实验：只为确认图集 URL 为空是否与 msToken 相关"""
import json
import random
import string
import sys
import time
from pathlib import Path
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dy_app import dy_fetch  # noqa: E402
from dy_app.dyc_sign.abogus import ABogus, BrowserFingerprintGenerator  # noqa: E402
from dy_app.dyc_sign.xbogus import XBogus  # noqa: E402

cj = json.loads((ROOT / "cookies.json").read_text("utf-8"))
ck = cj.get("cookies") or cj
if isinstance(ck, list):
    ck = {c["name"]: c["value"] for c in ck if c.get("value")}

AID_ID = "7680850290924851067"


def fake_ms(n=184):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n - 2)) + "=="


def one(extra_query, extra_cookie=None, aid="6383", ua_ver=None):
    ua = dy_fetch._pick_ua()
    ck2 = dict(ck)
    if extra_cookie:
        ck2.update(extra_cookie)
    q = dy_fetch.default_query(ck2)
    q["aweme_id"] = AID_ID
    q["aid"] = aid
    q.update(extra_query)
    query = urllib.parse.urlencode(q)
    fp = BrowserFingerprintGenerator.generate_fingerprint("Chrome")
    signer = ABogus(fp=fp, user_agent=ua)
    signed_q, _a, _u, _b = signer.generate_abogus(query, "")
    url = "https://www.douyin.com/aweme/v1/web/aweme/detail/?" + signed_q
    req = urllib.request.Request(url, headers={
        "User-Agent": ua, "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*", "Cookie": dy_fetch._cookie_header(ck2),
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    d = data.get("aweme_detail") or {}
    ims = d.get("images") or []
    if not ims:
        return "NO_IMAGES", 0
    first = ims[0]
    n_url = len(first.get("url_list") or [])
    n_dl = len(first.get("download_url_list") or [])
    smp = (first.get("url_list") or [None])[0]
    return f"url={n_url} dl={n_dl}", (smp or "")[:60]


variants = [
    ("param msToken(len164)", one({"msToken": fake_ms(164)})),
    ("param msToken(len184)", one({"msToken": fake_ms(184)})),
]
print("V1/V2 params:")
for name, r in variants:
    print(" ", name, "->", r)
time.sleep(6)

print("V3 cookie msToken + aid1128:")
print(" ", one({"msToken": fake_ms(184)}, extra_cookie={"msToken": fake_ms(184)}, aid="1128"))
time.sleep(6)

print("V4 无 msToken 但把 cookie 里空名 '' 键剔除:")
ck3 = {k: v for k, v in ck.items() if k}
print(" ", one({}, ck3))
