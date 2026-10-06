# -*- coding: utf-8 -*-
"""spike_dy_c v2 —— P0 单作品取流：详情页 SSR HTML 解析 + 可达性探测

结论路径：导航详情页 → 不依赖接口，从 tab.html 提取
  视频: "playAddr":[{"src":"https://...douyinstatic.com/obj/..."}]
  图集: <img src="https://p3-...douyinpic.com/...aweme_images...">
再以 Range 下载探测可达性。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dy_app import config  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
VIDEO_URL = "https://www.douyin.com/video/7650077314046364928"
NOTE_URL = "https://www.douyin.com/note/7420018490091851047"


def probe(url: str) -> str:
    if not url:
        return "(无 URL)"
    try:
        req = urllib.request.Request(url, method="GET", headers={
            "Range": "bytes=0-2047", "User-Agent": UA,
            "Referer": "https://www.douyin.com/"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            b = resp.read()
            return (f"HTTP {resp.status} bytes={len(b)} "
                    f"type={resp.headers.get('Content-Type') or '-'} "
                    f"len={resp.headers.get('Content-Length') or '-'}")
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:120]}"


def harvest(tab, url, label):
    print(f"\n===== {label} =====")
    print("url:", url)
    try:
        tab.get(url)
        time.sleep(6)
    except Exception as e:
        print("nav err", e)
    html = ""
    try:
        html = tab.html or ""
    except Exception:
        pass
    print("html len:", len(html))

    if label.startswith("视频"):
        # 视频详情多为 CSR：等待渲染后直接读 video 元素与页面全局数据
        print("final url:", tab.url)
        try:
            time.sleep(3)
            info = tab.run_js(r"""return JSON.stringify({
              href: location.href,
              title: document.title,
              videoSrc: (function(){var v=document.querySelector('video');return v? (v.src||(v.currentSrc)||''):'';})(),
              vids: Array.from(document.querySelectorAll('video')).map(function(v){return v.currentSrc||v.src;})
            })""")
            print("js info:", str(info)[:300])
        except Exception as e:
            print("js info err:", e)
        html = ""
        try:
            html = tab.html or ""
        except Exception:
            pass
        vids = re.findall(r'"playAddr":\[\{"src":"(https://[^"]+)"', html)
        seen = []
        for u in vids:
            if u not in seen:
                seen.append(u)
        print(f"playAddr 命中: {len(seen)}  html={len(html)}")
        for i, u in enumerate(seen[:2]):
            print(f"  video[{i}] {u[:150]}")
            print("     可达:", probe(u))
    else:
        import html as _html
        imgs = re.findall(r'src="(https://[^"]*douyinpic\.com/[^"]*aweme_images[^"]*)"', html)
        seen = []
        for u in imgs:
            u = _html.unescape(u)
            if u not in seen:
                seen.append(u)
        print(f"aweme_images 命中: {len(seen)}")
        for i, u in enumerate(seen[:3]):
            print(f"  img[{i}] {u[:150]}")
            print("     可达:", probe(u))


def main() -> int:
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions().set_address(f"127.0.0.1:{config.QT_CDP_PORT}")
    page = None
    for _ in range(20):
        try:
            page = ChromiumPage(co)
            break
        except Exception:
            time.sleep(0.5)
    if page is None:
        print("attach fail")
        return 1
    tabs = page.get_tabs(url="douyin.com")
    tab = tabs[0] if tabs else page
    print("tab:", tab.url)
    try:
        tab.listen.stop()
    except Exception:
        pass
    harvest(tab, VIDEO_URL, "视频详情(SSR)")
    harvest(tab, NOTE_URL, "图集详情(SSR)")
    print("\n[P0] spike_c v2 done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
