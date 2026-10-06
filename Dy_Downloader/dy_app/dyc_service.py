# -*- coding: utf-8 -*-
"""DyCollector sidecar 收割服务（被 Electron 主进程 spawn 调用）

流程：
  1. 挂接 Electron CDP(9357)；
  2. 通过 get_tabs(url='douyin.com') 选中已登录的抖音视图（多视图下不新建 tab）；
  3. 登录态检查（持久化优先）；
  4. 导航到目标页并滚动收割分页 XHR，解析作品清单；
  5. stdout 输出日志；末尾输出 ==DYC_JSON== + JSON（供主进程回传面板）。

用法：.venv\\Scripts\\python.exe dyc_service.py --target <url>
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dy_app import config, cookies as cookies_mod  # noqa: E402

SCROLL_ROUNDS = 10
SCROLL_STEP = 1600


def out(msg: str) -> None:
    print(msg, flush=True)


def attach_dy_tab():
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions().set_address(f"127.0.0.1:{config.QT_CDP_PORT}")
    page = None
    for _ in range(30):
        try:
            page = ChromiumPage(co)
            break
        except Exception:
            time.sleep(0.5)
    if page is None:
        raise RuntimeError("无法挂接 Electron CDP（请先启动 DyCollector）")
    tabs = page.get_tabs(url="douyin.com")
    if not tabs:
        raise RuntimeError("未找到抖音页面（请确认左侧浏览器已打开抖音）")
    return tabs[0]


def to_dict(body):
    if isinstance(body, dict):
        return body
    if isinstance(body, (str, bytes)):
        try:
            return json.loads(body)
        except Exception:
            return {}
    return {}


def parse_aweme(a: dict) -> dict:
    a = a or {}
    author = a.get("author") or {}
    stats = a.get("stats") or {}
    return {
        "aweme_id": str(a.get("aweme_id") or ""),
        "desc": (a.get("desc") or "")[:80],
        "kind": "images" if a.get("images") else "video",
        "author": author.get("nickname") or "",
        "digg": int(stats.get("digg_count") or 0),
        "create_time": int(a.get("create_time") or 0),
    }


def run(target: str) -> int:
    out(f"[sidecar] 目标: {target}")
    out("[sidecar] 挂接抖音视图…")
    try:
        tab = attach_dy_tab()
    except Exception as e:
        out(f"[sidecar] 失败: {e}")
        return 1

    # 登录态检查（不阻塞：仅提示）
    snap = cookies_mod.extract_from_tab(tab)
    if cookies_mod.has_login(snap):
        out("[sidecar] 登录态 OK（本地持久化）")
    else:
        out("[sidecar] 警告：当前抖音视图未检测到登录态，收藏类页面可能受限")

    out("[sidecar] 开始监听并导航…")
    try:
        tab.listen.start(config.LISTEN_KEYWORDS)
    except Exception as e:
        out(f"[sidecar] 启动监听失败: {e}")
        return 2
    try:
        tab.get(target)
        out("[sidecar] 已导航: " + str(tab.url))
    except Exception as e:
        out(f"[sidecar] 导航异常(继续): {e}")
    time.sleep(4)

    items, seen = [], set()

    def drain(timeout=0.5):
        try:
            for p in tab.listen.steps(timeout=timeout):
                body = to_dict(getattr(p.response, "body", None))
                data = body.get("data") or {}
                awemes = data.get("aweme_list")
                if not isinstance(awemes, list):
                    awemes = body.get("aweme_list") or []
                for a in awemes:
                    if not isinstance(a, dict) or not a.get("aweme_id"):
                        continue
                    aid = str(a["aweme_id"])
                    if aid in seen:
                        continue
                    seen.add(aid)
                    items.append(parse_aweme(a))
                if len(items) >= config.MAX_ITEMS:
                    break
        except Exception:
            pass

    drain(1.0)
    for _ in range(SCROLL_ROUNDS):
        if len(items) >= config.MAX_ITEMS:
            break
        try:
            tab.scroll.down(SCROLL_STEP)
        except Exception:
            pass
        time.sleep(0.9)
        drain(0.35)

    out(f"[sidecar] 完成：捕获作品 {len(items)} 条")
    payload = {"ok": True, "mode": "collect", "items": items[: config.MAX_ITEMS]}
    print("\n==DYC_JSON==\n" + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=config.FAVORITE_URL)
    args = ap.parse_args()
    return run(args.target)


if __name__ == "__main__":
    sys.exit(main())
