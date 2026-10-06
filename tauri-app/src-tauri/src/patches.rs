//! 补丁接口：免重打包热更新
//!
//! 每次调用引擎前先查补丁目录，命中则用补丁产物，否则用内置引擎。
//!
//! 补丁目录：
//! - 生产：`%APPDATA%\DownloadTooler\patches\`
//! - 开发：`tauri-app/patches/`
//!
//! 引擎是**多入口合并的 onedir 引擎**（`dt-engine`，按 `DT_TASK` 分发任务），
//! 所以补丁要么给一个独立 exe，要么给一整个重建的引擎目录。查找顺序：
//! 1. `<patches>/engine/<engine>-x86_64-pc-windows-msvc.exe`
//! 2. `<patches>/engine/<engine>.exe`
//! 3. `<patches>/engine/<engine>/<engine>.exe`（整目录替换，对齐 Electron 版约定）
//!
//! 说明：新架构下平台窗口是原生站点页面、**没有 Vue 面板**，可热更新的“资源”
//! 只有 Python 引擎产物，因此补丁接口只覆盖引擎。

use std::path::{Path, PathBuf};

/// 开发期补丁目录（tauri-app/patches），与 `paths` 模块保持一致
fn dev_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("patches")
}

/// 补丁根目录
pub fn root() -> PathBuf {
    if cfg!(debug_assertions) {
        dev_root()
    } else {
        std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join("DownloadTooler")
            .join("patches")
    }
}

/// 查找某个引擎的补丁产物；未命中返回 None
pub fn engine_override(engine: &str) -> Option<PathBuf> {
    let dir = root().join("engine");
    let candidates = [
        dir.join(format!("{}-x86_64-pc-windows-msvc.exe", engine)),
        dir.join(format!("{}.exe", engine)),
        dir.join(engine).join(format!("{}.exe", engine)),
    ];
    candidates.into_iter().find(|p| p.is_file())
}

/// 启动时输出已启用的补丁，便于排查
pub fn log_active(engines: &[&str]) {
    let root = root();
    println!("[patches] 补丁目录: {}（存在: {}）", root.display(), root.is_dir());
    for e in engines {
        match engine_override(e) {
            Some(p) => println!("[patches] 引擎补丁生效: {} → {}", e, p.display()),
            None => println!("[patches] {} 使用内置引擎", e),
        }
    }
}
