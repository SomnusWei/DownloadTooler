//! 单应用启动 + 按需打开 WebView 窗口
//!
//! 启动时仅创建 panel_main（Vue 控制台，不绑 CDP）。
//! dy_main / xhs_main 由前端通过 `open_platform_window` command 按需启动，
//! 各自独立 UserDataFolder + CDP 端口（独立浏览器进程）。
//!
//! 已知问题：若端口被其他进程占用作出站源端口（如 verge-mihomo 代理），
//! WebView2 静默放弃绑定 CDP。需选择不冲突的端口或临时关闭代理。

use crate::paths;
use std::path::PathBuf;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

const DY_CDP_PORT: u16 = 9357;
const XHS_CDP_PORT: u16 = 9361;
const DY_HOME: &str = "https://www.douyin.com";
const XHS_HOME: &str = "https://www.xiaohongshu.com";

/// 启动时仅创建控制面板窗口（无 CDP）
pub fn build_all(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    WebviewWindowBuilder::new(app, "panel_main", WebviewUrl::App("index.html".into()))
        .title("DownloadTooler - 控制面板")
        .inner_size(420.0, 600.0)
        .position(1320.0, 100.0)
        .resizable(true)
        .build()?;
    Ok(())
}

/// 按需启动抖音 / 小红书 WebView 窗口（各自独立 CDP 端口）
#[tauri::command]
pub async fn open_platform_window(
    app: tauri::AppHandle,
    platform: String,
) -> Result<String, String> {
    let label = match platform.as_str() {
        "dy" => "dy_main",
        "xhs" => "xhs_main",
        _ => return Err(format!("未知 platform: {}", platform)),
    };

    // 窗口已存在 → 直接聚焦
    if let Some(existing) = app.get_webview_window(label) {
        let _ = existing.set_focus();
        return Ok(format!("{} 已存在，已聚焦（CDP 端口 {}）", label, cdp_port_for(&platform).unwrap_or(0)));
    }

    let (cdp_port, home_url, title, data_dir, pos) = match platform.as_str() {
        "dy" => (DY_CDP_PORT, DY_HOME, "DownloadTooler - 抖音", paths::dy_user_data_dir(), (0.0, 0.0)),
        "xhs" => (XHS_CDP_PORT, XHS_HOME, "DownloadTooler - 小红书", paths::xhs_user_data_dir(), (100.0, 100.0)),
        _ => return Err(format!("未知 platform: {}", platform)),
    };

    ensure_dir(&data_dir).map_err(|e| format!("创建数据目录失败: {}", e))?;

    let args = format!("--remote-debugging-port={} --remote-allow-origins=*", cdp_port);
    let url = WebviewUrl::External(home_url.parse().map_err(|e| format!("URL 解析失败: {}", e))?);

    WebviewWindowBuilder::new(&app, label, url)
        .additional_browser_args(&args)
        .data_directory(data_dir)
        .title(title)
        .inner_size(1280.0, 800.0)
        .position(pos.0, pos.1)
        .build()
        .map_err(|e| format!("创建 {} 窗口失败: {}", label, e))?;

    Ok(format!("{} 已创建，CDP 端口 {}", label, cdp_port))
}

fn ensure_dir(p: &PathBuf) -> std::io::Result<()> {
    if !p.exists() {
        std::fs::create_dir_all(p)?;
    }
    Ok(())
}

/// 根据 platform 返回对应 CDP 端口
pub fn cdp_port_for(platform: &str) -> Option<u16> {
    match platform {
        "dy" => Some(DY_CDP_PORT),
        "xhs" => Some(XHS_CDP_PORT),
        _ => None,
    }
}
