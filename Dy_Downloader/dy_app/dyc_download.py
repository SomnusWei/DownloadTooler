# -*- coding: utf-8 -*-
"""DyCollector 下载引擎（纯后台，不触碰浏览器）

输入：--url <作品链接> --dir <下载根目录> [--quality N] [--cookies-file path]
流程：签名直连 detail 接口（dy_fetch）→ 无水印视频
      → 落盘 作者/标题_aweme_id/ → 流式下载、失败重试、半截清理。
版本策略：当前版本仅支持视频；图集/图文作品直接给出提示并退出（不另开窗口/不捕获）。
输出：stdout 日志；末尾 ==DYC_JSON== + JSON
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dy_app import config, cookies as cookies_mod  # noqa: E402
from dy_app import dy_fetch  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _force_utf8_stdio():
    """PyInstaller 打包后 PYTHONUTF8 可能不生效，stdio 会回落到 GBK：
    日志乱码，且 page_bridge 的大 JSON 应答（含中文）会解码失败。这里强制 UTF-8。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()


def out(m): print(m, flush=True)


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip().strip(".")
    return name[:120] or "untitled"


def dl(url, path: Path, retries: int = 3):
    last = None
    if path.exists() and path.stat().st_size > 1024 * 1024:
        out(f"  已存在，跳过: {path.name}（{path.stat().st_size} 字节）")
        return path.stat().st_size
    for i in range(retries):
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            req = urllib.request.Request(url, method="GET", headers={
                "User-Agent": UA, "Referer": "https://www.douyin.com/",
                "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
                total = 0
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except Exception:
                    total = 0
                n = 0
                last_pct = -1
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    n += len(chunk)
                    if total:
                        pct = int(n * 100 // total)
                        if pct - last_pct >= 2 or n >= total:
                            if pct != last_pct:
                                out(f"[prog] {pct}")
                                last_pct = pct
            if n <= 0:
                raise RuntimeError("empty body")
            tmp.replace(path)
            return n
        except Exception as e:
            last = e
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            out(f"  下载失败(重试 {i + 1}/{retries}): {type(e).__name__} {str(e)[:100]}")
            time.sleep(1.2 * (i + 1))
    raise last or RuntimeError("download failed")


def dl_img(url, path: Path, retries: int = 2):
    """下载单张图片（图集）。图片 URL 由接口签名下发，可直连，无需浏览器通道。"""
    if path.exists() and path.stat().st_size > 1024:
        return path.stat().st_size
    last = None
    for i in range(retries):
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            req = urllib.request.Request(url, method="GET", headers={
                "User-Agent": UA, "Referer": "https://www.douyin.com/",
                "Accept": "image/avif,image/webp,image/*,*/*"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
                n = 0
                while True:
                    chunk = resp.read(128 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    n += len(chunk)
            if n <= 0:
                raise RuntimeError("empty body")
            tmp.replace(path)
            return n
        except Exception as e:
            last = e
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            out(f"  图片下载失败(重试 {i + 1}/{retries}): {type(e).__name__} {str(e)[:90]}")
            time.sleep(0.8 * (i + 1))
    raise last or RuntimeError("image download failed")


def _kind_from_url(url):
    """URL 类型段：video → 视频；note/gallery/slides → 图集；其它留空（交给接口判定）。"""
    m = re.search(r"/(video|note|gallery|slides)/", url or "")
    if not m:
        return ""
    return "video" if m.group(1) == "video" else "images"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=False)
    ap.add_argument("--dir", default="")
    ap.add_argument("--quality", default="", help="期望清晰度(如1080)，空=最高")
    ap.add_argument("--cookies-file", default="", help="cookies.json 路径(默认 config.COOKIES_FILE)")
    args = ap.parse_args()

    cpath = Path(args.cookies_file) if args.cookies_file else None
    keeper = cookies_mod.CookieKeeper(cpath)
    cookies = keeper.load()
    if not cookies:
        out(f"[dl] cookies.json 为空/缺失: {keeper.path}")

    err = None
    meta = {"type": _kind_from_url(args.url) or "video", "title": "", "author": "未知", "quality": ""}
    medias = []
    out("[dl] 签名取流 detail…")
    try:
        meta, medias = dy_fetch.fetch_detail(args.url, cookies, args.quality)
    except Exception as e:
        err = e
        out(f"[dl] 取流失败: {e}")

    # 图集无图片地址：仅当接口确实返回 images 结构却取不到 URL 时才失败
    if not medias:
        if meta["type"] == "images":
            out("[dl] 图集未取到图片地址（可能风控或接口结构变化）")
            print(json.dumps({"ok": False, "type": "images", "err": "图集无图片地址"}, ensure_ascii=False), flush=True)
            return 8
        out("[dl] 未取到视频媒体/ID")
        print(json.dumps({"ok": False, "err": str(err)[:200] if err else "no-video-media"},
                         ensure_ascii=False), flush=True)
        return 1
    if not meta.get("aweme_id") and args.url:
        mm = re.search(r"/(?:video|note|gallery|slides)/(\d+)", args.url)
        if mm:
            meta["aweme_id"] = mm.group(1)
    if meta["type"] == "images":
        out(f"[dl] 图集 · {meta['author']} · {meta['aweme_id']} · {len(medias)} 张")
    else:
        out(f"[dl] video · {meta['author']} · {meta['aweme_id']} · "
            + (f"清晰度 {meta.get('quality')}" if meta.get('quality') else "最高清晰度"))

    base = Path(args.dir) if args.dir else (config.DATA_DIR / "Downloaded")
    # 落盘规则：目标目录 / 作者名 / 作品名
    title = sanitize(meta["title"]) or meta["aweme_id"]
    folder = base / sanitize(meta["author"]) / title
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        out(f"建目录失败: {e}")
        print(json.dumps({"ok": False, "err": "mkdir"}, ensure_ascii=False))
        return 1

    if meta["type"] == "images":
        total = len(medias)
        files = []
        for i, (u, ext) in enumerate(medias, 1):
            name = folder / f"{i:02d}.{ext}"
            try:
                size = dl_img(u, name)
                files.append({"name": name.name, "bytes": size})
                out(f"  图片 {i}/{total}: {name.name} ({size} 字节)")
            except Exception as e:
                out(f"  图片 {i}/{total} 失败: {str(e)[:100]}")
            out(f"[prog] {int(i * 100 / total)}")
        if not files:
            print(json.dumps({"ok": False, "type": "images", "err": "图集下载全部失败",
                              "folder": str(folder)}, ensure_ascii=False))
            return 2
        payload = {"ok": True, "id": meta["aweme_id"], "type": "images",
                   "title": meta["title"], "author": meta["author"],
                   "folder": str(folder), "files": files,
                   "total_bytes": sum(f["bytes"] for f in files)}
        out(f"完成: {len(files)} 张图片 → {folder}")
        print("\n==DYC_JSON==\n" + json.dumps(payload, ensure_ascii=False), flush=True)
        return 0

    files = []
    name = folder / "视频.mp4"
    try:
        size = dl(medias[0][0], name)
        files.append({"name": name.name, "bytes": size})
        out(f"视频完成: {name} ({size})")
    except Exception as e:
        out(f"视频下载失败: {e}")
        print(json.dumps({"ok": False, "err": str(e)[:200], "folder": str(folder)},
                         ensure_ascii=False))
        return 2
    if not files:
        print(json.dumps({"ok": False, "err": "no-file-downloaded"}, ensure_ascii=False))
        return 3

    payload = {"ok": True, "id": meta["aweme_id"], "type": meta["type"],
               "title": meta["title"], "author": meta["author"],
               "folder": str(folder), "files": files,
               "total_bytes": sum(f["bytes"] for f in files)}
    out(f"完成: {len(files)} 个文件 → {folder}")
    print("\n==DYC_JSON==\n" + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
