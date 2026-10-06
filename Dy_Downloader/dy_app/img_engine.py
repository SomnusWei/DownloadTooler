# -*- coding: utf-8 -*-
"""img_engine —— 图集/图文“页面驱动”取图引擎（独立模块）

背景：抖音 detail 接口对图集 URL 的发放卡在登录会话/指纹/频控后，纯后台 HTTP
经常拿到空 URL / 403。但浏览器页面自己的请求能正常拿到图——页面把完整带签名
图 URL 以“内联 <script> 文本 + JS 转义(https:\\u002f\\u002f…)”注入 HTML。

因此图集改由本模块驱动：优先复用 Electron 同登录 CDP 新开标签打开作品页，
从页面 SSR 文本解码抽取全部图 URL；并识别“扫码登录 / 验证码”等登录墙，
把状态如实返回给上层（上层据此自动暂停队列，提示人工登录）。

对外接口：
    page_pick(url, cdp="127.0.0.1:9357") -> {urls, author, title}
      成功：urls 非空；
      失败：raise RuntimeError，文案含“登录墙/验证码”类关键词供上层识别。
"""
import json
import re
import time

_CDP = "127.0.0.1:9357"


def _decode_dy(t):
    t = t.replace("\\u002f", "/").replace("\\u0026", "&")
    return t.replace("&amp;", "&")


def _pick_dy_field(dec, key):
    m = re.search(r'"' + key + r'":"((?:[^"\\]|\\.)*)"', dec)
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return m.group(1)


def _extract(dec):
    seen, urls = set(), []
    for m in re.finditer(r'https://[^\s"\'<>]+douyinpic\.com/tos[^\s"\'<>]*', dec):
        u = m.group(0)
        if "aweme_images" not in u:
            continue
        key = u.split("~tplv")[0].split("?")[0]
        if key not in seen:
            seen.add(key)
            urls.append(u)
        if len(urls) >= 120:
            break
    return urls


def page_pick(url, cdp=_CDP):
    """同登录 CDP 新开标签 → 打开作品页 → SSR/接口文本解码抽图。失败抛异常。"""
    from DrissionPage import Chromium, ChromiumOptions

    co = ChromiumOptions().set_address(cdp).set_timeouts(base=5, page_load=30, script=8)
    b = Chromium(co)
    tab = None
    try:
        tab = b.new_tab()
        tab.get(url)
        # SSR 脚本在首包 HTML 里，等主进程就绪即可；最多轮询几秒等慢网
        urls = []
        for _ in range(6):
            time.sleep(1.2)
            t = (tab.title or "") + " | " + (tab.url or "")
            dec = _decode_dy(tab.html)
            urls = _extract(dec)
            if urls:
                break
            # 登录墙 / 验证码中间页：SSR 里不会有图
            if any(w in dec or w in t for w in ("扫码登录", "登录后查看", "请先登录", "安全验证")):
                raise RuntimeError("登录墙：请在左侧浏览器扫码登录后重试")
            if "验证码" in t:
                raise RuntimeError("登录墙：当前会话需人工完成验证码")
        if not urls:
            raise RuntimeError("SSR 未含 aweme_images（疑似频控/未知页面结构）")
        return {
            "urls": urls,
            "author": _pick_dy_field(dec, "nickname"),
            "title": _pick_dy_field(dec, "desc"),
        }
    finally:
        if tab is not None:
            try:
                b.close_tabs(tab)
            except Exception:
                pass
