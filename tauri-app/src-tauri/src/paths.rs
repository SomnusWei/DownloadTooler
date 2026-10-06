//! 路径解析模块
//!
//! 开发期：UserDataFolder 写在 tauri-app/{dy,xhs}_data 下；cookies.json
//!        写到原 Electron 子项目根目录（兼容 Python sidecar 零改动）。
//! 生产期：UserDataFolder 写在 %LOCALAPPDATA%/DownloadTooler/{dy,xhs}_data；
//!        cookies.json 写在 %LOCALAPPDATA%/{DyCollector,XHSCollector}/cookies.json。

use std::path::{Path, PathBuf};

/// src-tauri/ 目录（CARGO_MANIFEST_DIR）
const fn manifest_dir() -> &'static str {
    env!("CARGO_MANIFEST_DIR")
}

/// tauri-app/ 目录（开发期项目根）
fn project_root() -> PathBuf {
    Path::new(manifest_dir())
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// DownloadTooler/ 目录（tauri-app 的父目录）
fn repo_root() -> PathBuf {
    Path::new(manifest_dir())
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// 抖音 WebView2 UserDataFolder
pub fn dy_user_data_dir() -> PathBuf {
    if cfg!(debug_assertions) {
        // 开发期：tauri-app/dy_data
        project_root().join("dy_data")
    } else {
        // 生产期：%LOCALAPPDATA%/DownloadTooler/dy_data
        local_app_data().join("DownloadTooler").join("dy_data")
    }
}

/// 小红书 WebView2 UserDataFolder
pub fn xhs_user_data_dir() -> PathBuf {
    if cfg!(debug_assertions) {
        project_root().join("xhs_data")
    } else {
        local_app_data().join("DownloadTooler").join("xhs_data")
    }
}

/// 控制面板 WebView2 UserDataFolder（CDP 端口 9357 绑定在此窗口上）
pub fn panel_user_data_dir() -> PathBuf {
    if cfg!(debug_assertions) {
        project_root().join("panel_data")
    } else {
        local_app_data().join("DownloadTooler").join("panel_data")
    }
}

/// 控制面板 settings.json 路径（下载目录、快捷键绑定等本地持久化配置）
pub fn settings_file() -> PathBuf {
    panel_user_data_dir().join("settings.json")
}

/// 默认下载目录（用户未设置时的回退值）
#[allow(dead_code)]
pub fn default_download_dir() -> PathBuf {
    if let Some(downloads) = std::env::var_os("USERPROFILE") {
        let mut p = PathBuf::from(downloads);
        p.push("Downloads");
        if p.exists() {
            return p;
        }
    }
    if cfg!(debug_assertions) {
        project_root().join("downloads")
    } else {
        local_app_data().join("DownloadTooler").join("downloads")
    }
}

/// 抖音 cookies.json 路径
pub fn dy_cookie_file() -> PathBuf {
    if cfg!(debug_assertions) {
        // 开发期：DownloadTooler/Dy_Downloader/cookies.json（兼容 Python sidecar）
        repo_root().join("Dy_Downloader").join("cookies.json")
    } else {
        local_app_data().join("DyCollector").join("cookies.json")
    }
}

/// 小红书 cookies.json 路径
pub fn xhs_cookie_file() -> PathBuf {
    if cfg!(debug_assertions) {
        repo_root().join("XHS_Downloader").join("cookies.json")
    } else {
        local_app_data().join("XHSCollector").join("cookies.json")
    }
}

/// 根据 platform 字符串返回 cookie 文件路径
pub fn cookie_file_for(platform: &str) -> PathBuf {
    match platform {
        "dy" => dy_cookie_file(),
        "xhs" => xhs_cookie_file(),
        _ => PathBuf::from(format!("{}_cookies.json", platform)),
    }
}

fn local_app_data() -> PathBuf {
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        PathBuf::from(local)
    } else {
        PathBuf::from(".")
    }
}

/// 确保 cookies.json 父目录存在
pub fn ensure_parent_dir(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() && !parent.exists() {
            std::fs::create_dir_all(parent)?;
        }
    }
    Ok(())
}
