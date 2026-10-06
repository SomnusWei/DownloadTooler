# -*- coding: utf-8 -*-
"""DownloadTooler 多入口引擎启动器

一个可执行文件承载多个引擎任务，按环境变量 `DT_TASK`（或首个位置参数）分发。

Rust 侧用法（注入 DT_TASK，其余参数照旧）：
    DT_TASK=dy-download   dt-engine.exe --url <作品> --dir <目录> --cookies-file <cookies.json>
    DT_TASK=xhs-note      dt-engine.exe --href <笔记链接>
    DT_TASK=xhs-download  dt-engine.exe --note-json <file> --dir <目录>

合并目的：三个引擎共用同一份 Python 运行时与 curl_cffi / lxml 等依赖，
避免 onefile 三次重复打包；配合 onedir 还可免去每次启动的解包开销。
"""
import importlib
import os
import sys

# 任务 → (模块名, 入口函数)
_TASKS = {
    "dy-download": ("dy_app.dyc_download", "main"),
    "xhs-note": ("xhs_engine.note_one", "main"),
    "xhs-download": ("xhs_engine.download_one", "main"),
}


def _dispatch() -> int:
    task = (os.environ.pop("DT_TASK", "") or "").strip()
    if not task and len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        # 也支持 `dt-engine <task> ...` 形式，便于手工调试
        task = sys.argv[1].strip()
        del sys.argv[1]
    if not task:
        sys.stderr.write("DT_TASK 未指定（可选 %s）\n" % " / ".join(_TASKS))
        return 2

    entry = _TASKS.get(task)
    if not entry:
        sys.stderr.write("未知 DT_TASK: %s\n" % task)
        return 2

    mod = importlib.import_module(entry[0])
    return int(getattr(mod, entry[1])() or 0)


if __name__ == "__main__":
    sys.exit(_dispatch())
