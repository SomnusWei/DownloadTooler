# -*- coding: utf-8 -*-
"""由 logo.jpeg 生成多尺寸 logo.ico（打包 exe / 窗口图标统一用 logo.ico）"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "logo.jpeg"
DST = ROOT / "logo.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"缺少图标源图: {SRC}")
    img = Image.open(SRC)
    if img.mode in ("P", "L"):
        img = img.convert("RGBA")
    elif img.mode == "RGB":
        img = img.convert("RGBA")
    img.save(DST, format="ICO", sizes=SIZES)
    print(f"已生成 {DST}（含尺寸 {[s[0] for s in SIZES]}）")


if __name__ == "__main__":
    main()
