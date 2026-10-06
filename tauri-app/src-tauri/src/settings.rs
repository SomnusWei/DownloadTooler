//! 配置中心本地持久化模块
//!
//! 存储内容：
//! - download_dir: XHS / DY 共用的下载目录
//! - shortcuts: 窗口启动/聚焦全局快捷键（dy / xhs 各自独立绑定）
//! - download_shortcut: 下载快捷键（窗口内生效，默认 Ctrl+D）
//! - dy_quality: 抖音清晰度档位（"" = 最高）
//!
//! 文件位置：panel_data/settings.json（与控制面板 WebView2 共用 UserDataFolder 父目录）

use crate::paths;
use serde::{Deserialize, Serialize};

/// 持久化配置根结构
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    /// 下载目录（XHS / DY 共用），空字符串代表使用默认值
    #[serde(default)]
    pub download_dir: String,

    /// 窗口快捷键绑定
    #[serde(default)]
    pub shortcuts: Shortcuts,

    /// 下载快捷键（仅平台窗口聚焦时生效；空字符串 = 禁用）
    #[serde(default = "default_download_shortcut")]
    pub download_shortcut: String,

    /// 抖音清晰度档位："" = 最高，或 "1080" / "720" / "540"
    #[serde(default)]
    pub dy_quality: String,
}

/// 平台 → 快捷键字符串映射
/// 字符串格式遵循 Tauri 2.0 global-shortcut Shortcut 解析规则
/// 例如 "Alt+1"、"Ctrl+Shift+D"、"CommandOrControl+2"
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Shortcuts {
    #[serde(default = "default_dy_shortcut")]
    pub dy: String,

    #[serde(default = "default_xhs_shortcut")]
    pub xhs: String,
}

fn default_dy_shortcut() -> String {
    "Alt+1".into()
}

fn default_xhs_shortcut() -> String {
    "Alt+2".into()
}

/// 下载快捷键默认值（对齐 Electron 版 Ctrl+D）
fn default_download_shortcut() -> String {
    "Ctrl+D".into()
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            download_dir: String::new(),
            shortcuts: Shortcuts::default(),
            download_shortcut: default_download_shortcut(),
            dy_quality: String::new(),
        }
    }
}

impl Default for Shortcuts {
    fn default() -> Self {
        Self {
            dy: default_dy_shortcut(),
            xhs: default_xhs_shortcut(),
        }
    }
}

/// 从 settings.json 读取配置；文件不存在或解析失败时返回默认值
pub fn load() -> Settings {
    let file = paths::settings_file();
    match std::fs::read_to_string(&file) {
        Ok(text) => match serde_json::from_str::<Settings>(&text) {
            Ok(s) => s,
            Err(e) => {
                eprintln!(
                    "[settings] 解析 {} 失败: {}，使用默认值",
                    file.display(),
                    e
                );
                Settings::default()
            }
        },
        Err(_) => Settings::default(),
    }
}

/// 写入 settings.json
pub fn save(s: &Settings) -> Result<(), String> {
    let file = paths::settings_file();
    if let Some(parent) = file.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("创建目录失败: {}", e))?;
    }
    let text = serde_json::to_string_pretty(s).map_err(|e| format!("序列化失败: {}", e))?;
    std::fs::write(&file, text).map_err(|e| format!("写文件失败: {}", e))?;
    Ok(())
}

/// 取当前下载目录（若用户未设置则回退到默认）
pub fn download_dir() -> std::path::PathBuf {
    let s = load();
    if s.download_dir.is_empty() {
        paths::default_download_dir()
    } else {
        std::path::PathBuf::from(s.download_dir)
    }
}

/// 读取配置（前端调用）
#[tauri::command]
pub async fn get_settings() -> Result<Settings, String> {
    Ok(load())
}

/// 设置下载目录（持久化 + 立即生效）
#[tauri::command]
pub async fn set_download_dir(dir: String) -> Result<Settings, String> {
    let mut s = load();
    s.download_dir = dir;
    save(&s)?;
    Ok(s)
}

/// 设置平台快捷键（unregister 旧的 + on_shortcut 新的 + 持久化）
/// 传入空字符串表示禁用该平台快捷键
#[tauri::command]
pub async fn set_shortcut(
    app: tauri::AppHandle,
    platform: String,
    shortcut: String,
) -> Result<Settings, String> {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;

    let mut s = load();
    let old = match platform.as_str() {
        "dy" => s.shortcuts.dy.clone(),
        "xhs" => s.shortcuts.xhs.clone(),
        _ => return Err(format!("未知 platform: {}", platform)),
    };

    // 先取消旧的（on_shortcut 注册的快捷键也用 unregister 字符串取消）
    let gs = app.global_shortcut();
    if !old.is_empty() {
        let _ = gs.unregister(old.as_str());
    }

    // 用 on_shortcut 注册新的（绑定特定 handler，无需字符串匹配）
    crate::register_platform_shortcut(&app, &platform, &shortcut)
        .map_err(|e| format!("注册快捷键失败: {}（格式例如 Alt+1、Ctrl+Shift+D）", e))?;

    match platform.as_str() {
        "dy" => s.shortcuts.dy = shortcut,
        "xhs" => s.shortcuts.xhs = shortcut,
        _ => {}
    }
    save(&s)?;
    Ok(s)
}

/// 设置下载快捷键（空字符串 = 禁用）
///
/// 该快捷键**仅在平台窗口聚焦时**注册（见 lib.rs 的 `refresh_download_shortcut`），
/// 保存后按当前聚焦状态立即重注册。
#[tauri::command]
pub async fn set_download_shortcut(
    app: tauri::AppHandle,
    shortcut: String,
) -> Result<Settings, String> {
    let mut s = load();
    s.download_shortcut = shortcut;
    save(&s)?;
    crate::refresh_download_shortcut(&app);
    Ok(s)
}

/// 设置抖音清晰度档位："" = 最高，或 "1080" / "720" / "540"
#[tauri::command]
pub async fn set_dy_quality(quality: String) -> Result<Settings, String> {
    let mut s = load();
    s.dy_quality = quality;
    save(&s)?;
    Ok(s)
}
