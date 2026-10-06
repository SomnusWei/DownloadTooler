# -*- coding: utf-8 -*-
"""从 logo.jpeg 生成多尺寸 XHSCollector.ico（输出到项目根）"""
import sys
from pathlib import Path

try:
    from PIL import Image
except Exception as e:
    sys.exit("Pillow 不可用: %s" % e)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "logo.jpeg"
OUT = ROOT / "XHSCollector.ico"
if not SRC.exists():
    sys.exit("缺少 logo.jpeg")

img = Image.open(SRC).convert("RGBA")
# 居中裁成正方形
w, h = img.size
s = min(w, h)
img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
sizes = [16, 24, 32, 48, 64, 128, 256]
img.save(OUT, format="ICO", sizes=[(x, x) for x in sizes])
print("generated:", OUT)
