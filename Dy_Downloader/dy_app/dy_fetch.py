# -*- coding: utf-8 -*-
"""dy_fetch —— 纯后台 HTTP 取流（借鉴 douyin-downloader MIT 签名工具）

不依赖浏览器页面：Python 端按抖音 web 协议构造参数并生成 a_bogus 签名，
直接请求详情接口，拿到无水印视频 / 图集原图。

入口：fetch_detail(url, cookies: dict) -> (meta, medias)
  meta  : {aweme_id, type(video/images), title, author, create_time}
  medias: [(url, ext)]
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dy_app import config  # noqa: E402
from dy_app import dy_bridge  # noqa: E402
from dy_app.dyc_sign.abogus import ABogus, BrowserFingerprintGenerator  # noqa: E402
from dy_app.dyc_sign.ms_token import ensure_ms_token  # noqa: E402
from dy_app.dyc_sign.xbogus import XBogus  # noqa: E402

BASE = "https://www.douyin.com"
PATH = "/aweme/v1/web/aweme/detail/"
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]
import random as _random


def _pick_ua() -> str:
    global UA
    UA = _random.choice(UA_POOL)
    return UA


def default_query(cookies: dict) -> dict:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "update_version_code": "170400",
        "pc_client_type": "2",
        "pc_libra_divert": "Windows",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "2048",
        "screen_height": "1152",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "131.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "131.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "32",
        "device_memory": "32",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "50",
        "support_h265": "0",
        "support_dash": "1",
        # uifid 是抖音的设备指纹（Cookie UIFID/UIFID_TEMP），Argus 门禁会校验；
        # 缺省空串会直接回 403 Blocked by ArgusSecurityPlugin Uifid Not Found
        "uifid": cookies.get("UIFID") or cookies.get("UIFID_TEMP") or cookies.get("uifid") or "",
        "msToken": cookies.get("msToken") or "",
    }


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


def _http_get(url: str, cookies: dict) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": _cookie_header(cookies),
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            snippet = e.read().decode("utf-8", "replace")[:180]
        except Exception:
            snippet = ""
        raise RuntimeError(f"HTTP {e.code}: {snippet or e.reason}") from e


def _sign_url(params: dict) -> str:
    query = urllib.parse.urlencode(params)
    fp = BrowserFingerprintGenerator.generate_fingerprint("Chrome")
    try:
        signer = ABogus(fp=fp, user_agent=UA)
        signed_q, _ab, _ua, _body = signer.generate_abogus(query, "")
        if signed_q and "a_bogus=" in signed_q:
            return f"{BASE}{PATH}?{signed_q}"
    except Exception as e:
        print(f"[sign] a_bogus 失败退避 X-Bogus: {e}", flush=True)
    url = f"{BASE}{PATH}?{query}"
    signed_url, _x, _u = XBogus(UA).build(url)
    return signed_url


def _plain_url(params: dict) -> str:
    """未签名 URL —— 供 page_bridge 使用：抖音页面内的请求由页面自身签名，
    再叠加本地的 a_bogus/X-Bogus 会被判 ``Sign Invalid``。"""
    return f"{BASE}{PATH}?{urllib.parse.urlencode(params)}"


def _urls_of(obj) -> list:
    """兼容抖音多种字段形态取 URL 列表：
    ``["url", …]`` / ``{"url_list": ["url", …]}`` / 裸字符串。
    """
    if isinstance(obj, str):
        return [obj] if obj else []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, str) and x]
    if isinstance(obj, dict):
        for k in ("url_list", "urlList", "download_url_list", "url"):
            v = obj.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, str) and x]
            if isinstance(v, str) and v:
                return [v]
    return []


def pick_first(obj) -> str:
    lst = _urls_of(obj)
    if lst:
        return lst[0]
    return obj.get("url") or "" if isinstance(obj, dict) else ""


def parse_detail(detail: dict, quality: str = ""):
    meta = {
        "aweme_id": str(detail.get("aweme_id") or ""),
        "title": (detail.get("desc") or "")[:120],
        "author": ((detail.get("author") or {}).get("nickname") or "未知"),
        "create_time": int(detail.get("create_time") or 0),
        "type": "video",
    }
    # 图集优先：存在 images 即按图集（纯视频作品无 images 数组；图集可能带封面动态视频）
    imgs = detail.get("images") or []
    if imgs:
        meta["type"] = "images"
        urls = []
        for im in imgs:
            # 字段优先级：url_list（无水印原图）→ origin/display → download_url_list（带水印兜底）
            u = ""
            for key in ("url_list", "origin_image", "display_image", "download_url_list"):
                lst = _urls_of(im.get(key))
                if lst:
                    u = lst[0]
                    break
            if not u:
                v = im.get("url")
                u = v if isinstance(v, str) else ""
            if u and u not in urls:
                urls.append(u)
        if not urls:
            print(f"[fetch] images 字段提取为空，样本 keys={list((imgs[0] or {}).keys())}", flush=True)
            return meta, []          # 有 images 结构但无 URL：不降级为视频，交由上层重试
        ext = "webp" if ("~tplv" in urls[0] or ".webp" in urls[0]) else "jpg"
        return meta, [(u, ext) for u in urls[:120]]
    if detail.get("video"):
        v = detail.get("video") or {}
        url = ""
        quality = ""
        cands = []
        for b in (v.get("bit_rate") or []):
            pa = b.get("play_addr") if isinstance(b.get("play_addr"), dict) else {}
            u = pick_first(pa)
            if u:
                gear = str(b.get("gear_name") or "")
                import re as _re
                res = max([int(x) for x in _re.findall(r"\d+", gear)] or [0])
                cands.append((res, int(b.get("bit_rate") or 0), gear, u))
        cands.sort(key=lambda x: (x[0], x[1]), reverse=True)   # 分辨率优先，其次码率
        try:
            want = int(quality or 0)
        except Exception:
            want = 0
        if want:
            for c in cands:
                if c[0] == want:
                    url, quality = c[3], c[2]
                    break
            else:
                url, quality = cands[0][3], cands[0][2]
        elif cands:
            url, quality = cands[0][3], cands[0][2]
        if not url:
            url = pick_first(v.get("play_addr") or {})
        if url:
            meta["quality"] = quality or ""
            if "playwm" in url:
                url = url.replace("/playwm/", "/play/")
            return meta, [(url, "mp4")]
    return meta, []


def _kind_from_url(url: str) -> str:
    for part in ("video",):
        if f"/{part}/" in url:
            return "video"
    return "note"   # note/gallery/slides 统一按 note 处理（图集用 aid=6383）


def _detail_from(data: dict):
    if not isinstance(data, dict):
        return None
    d = data.get("aweme_detail") or (data.get("data") or {}).get("aweme_detail")
    return d if isinstance(d, dict) else None


def fetch_detail(url: str, cookies: dict, quality: str = ""):
    """返回 (meta, medias)；失败抛异常。quality: 期望清晰度数字(如 1080)，空=最高

    取流两条路（先直连，被 Argus 风控拦掉时改走页面通道）：
      1) 直连：a_bogus/X-Bogus 签名 + Cookie 直接请求 detail 接口；
      2) page_bridge：交主进程在已登录隐藏窗口内以同源 fetch 请求（同签名 URL）。
    """
    m = None
    for part in ("video", "note", "gallery", "slides"):
        idx = url.find(f"/{part}/")
        if idx >= 0:
            m = (part, url[idx + len(part) + 2:].split("/")[0].split("?")[0])
            break
    if not m:
        raise RuntimeError("无法从 URL 识别作品")
    aweme_id = m[1]

    cookies = dict(cookies or {})
    bridge_on = dy_bridge.available()
    # aid 顺序：图集(note) 先 6383；视频同样先 6383（1128 兜底）
    aids = ("6383", "1128")
    last = None
    for aid in aids:
        for attempt in range(2):   # 容忍偶发 403
            _pick_ua()                             # 轮换 UA/指纹，降低风控判定
            if not cookies.get("msToken"):
                tok = ensure_ms_token(cookies, UA)
                if tok:
                    cookies["msToken"] = tok
                    out_tag = "真实" if len(tok) in (164, 184) else "随机"
                    print(f"[fetch] Cookie 缺 msToken，已生成{out_tag} token", flush=True)
            params = default_query(cookies)
            params["aweme_id"] = aweme_id
            params["aid"] = aid
            signed = _sign_url(params)

            # ① 直连（签名 + Cookie）
            try:
                detail = _detail_from(_http_get(signed, cookies))
                if detail:
                    return parse_detail(detail, quality)
                last = RuntimeError("detail 接口无有效返回(可能被风控)")
            except Exception as e:
                last = e

            # ② 页面通道：同源 fetch（绕过 ArgusSecurityPlugin 对非页面请求的 403）
            #    注意用「未签名 URL」：页面网络层会自行完成签名
            if bridge_on:
                try:
                    res = dy_bridge.request(_plain_url(params), timeout=35)
                    raw = res.get("text") or ""
                    print(f"[fetch] bridge id 应答 http={res.get('http_status')} bytes={len(raw)} "
                          f"head={raw[:80]!r}", flush=True)
                    detail = _detail_from(json.loads(raw) if raw else {})
                    if detail:
                        print("[fetch] 直连受阻，已改经 page_bridge 取流", flush=True)
                        return parse_detail(detail, quality)
                    last = RuntimeError("page_bridge 返回无 aweme_detail")
                except Exception as e:
                    last = e
            time.sleep(1.0)

    if last:
        raise last
    raise RuntimeError("detail 接口无有效返回(可能被风控)")
