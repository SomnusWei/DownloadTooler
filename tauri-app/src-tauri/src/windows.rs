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

    // WebView2 默认对 target=_blank / window.open 的新窗口请求「标记为已处理但不创建窗口」，
    // 页面表现为点击无反应。这里显式接管：站内链接新建受管窗口（共享同一 WebView2 环境，
    // 登录态不丢），站外链接交给系统浏览器。
    let popup_app = app.clone();
    let popup_platform = platform.clone();

    WebviewWindowBuilder::new(&app, label, url)
        .additional_browser_args(&args)
        .data_directory(data_dir)
        .title(title)
        .inner_size(1280.0, 800.0)
        .position(pos.0, pos.1)
        .initialization_script(NEW_WINDOW_CLICK_JS)
        .on_new_window(move |url, features| {
            open_managed_popup(&popup_app, &popup_platform, url, features)
        })
        .build()
        .map_err(|e| format!("创建 {} 窗口失败: {}", label, e))?;

    Ok(format!("{} 已创建，CDP 端口 {}", label, cdp_port))
}

/// 站内链接新窗口统一处理（主窗口与弹窗共用）
///
/// 弹窗内再次点击 `target=_blank` 链接时同样要走这里，否则新弹窗
/// 的 webview 没有 `on_new_window` 处理器，点击又会静默失效。
fn open_managed_popup(
    app: &tauri::AppHandle,
    platform: &str,
    url: tauri::Url,
    features: tauri::webview::NewWindowFeatures,
) -> tauri::webview::NewWindowResponse<tauri::Wry> {
    println!("[windows] new-window: {} ({})", url, platform);
    if !is_platform_host(platform, &url) {
        // 站外链接（含 http/https）交系统浏览器，避免在应用内打开无关站点
        if matches!(url.scheme(), "http" | "https") {
            open_in_system_browser(&url);
        }
        return tauri::webview::NewWindowResponse::Deny;
    }
    let label = next_popup_label(app, platform);
    let name = if platform == "dy" { "抖音" } else { "小红书" };
    let next_app = app.clone();
    let next_platform = platform.to_string();
    match WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .inner_size(1024.0, 720.0)
        // Windows 上 Create 必须复用 opener 的 WebView2 环境（含调试端口）
        .window_features(features)
        .title(format!("DownloadTooler - {} · 新窗口", name))
        .initialization_script(NEW_WINDOW_CLICK_JS)
        .on_new_window(move |url, features| {
            open_managed_popup(&next_app, &next_platform, url, features)
        })
        .build()
    {
        Ok(window) => {
            // WebView2 建窗后不一定把窗口激活；不主动聚焦的话窗口收不到焦点事件，
            // 聚焦域快捷键就不会注册（表现为弹窗里按快捷键没反应）
            let _ = window.set_focus();
            tauri::webview::NewWindowResponse::Create { window }
        }
        Err(e) => {
            eprintln!("[windows] 新建站内窗口失败: {}", e);
            tauri::webview::NewWindowResponse::Deny
        }
    }
}

/// 页面注入：把 `a[target=_blank]` 的点击改走 `window.open`
///
/// WebView2 对锚点的默认新窗口行为会被 wry 直接吞掉（`SetHandled(true)` 但不建窗口），
/// 表现为“点击无反应”；而 `window.open` 通路经 `on_new_window` 已验证可正常建窗。
/// 因此在 document-start 阶段捕获点击，统一改走 `window.open`。
const NEW_WINDOW_CLICK_JS: &str = r#"(function(){
  if (window.__dtNewWindowHooked) return;
  window.__dtNewWindowHooked = true;
  document.addEventListener('click', function(e){
    var t = e.target;
    if (!t || !t.closest) return;
    var a = t.closest('a[target="_blank"]');
    if (!a) return;
    var href = a.href || a.getAttribute('href') || '';
    if (!/^https?:/i.test(href)) return;
    e.preventDefault();
    e.stopPropagation();
    try { window.open(href, '_blank'); } catch (err) {}
  }, true);
})()"#;

/// 站内域名判定：仅这些主机的链接才在应用内新开窗口，其余交给系统浏览器
fn is_platform_host(platform: &str, url: &tauri::Url) -> bool {
    let Some(host) = url.host_str() else { return false };
    let host = host.to_ascii_lowercase();
    match platform {
        "dy" => {
            host == "douyin.com"
                || host.ends_with(".douyin.com")
                || host.ends_with(".iesdouyin.com")
        }
        "xhs" => host == "xiaohongshu.com" || host.ends_with(".xiaohongshu.com"),
        _ => false,
    }
}

/// 弹窗窗口 label：`dy_pop_1`、`dy_pop_2`…（窗口关闭后编号可复用）
fn next_popup_label(app: &tauri::AppHandle, platform: &str) -> String {
    for i in 1..=50 {
        let label = format!("{}_pop_{}", platform, i);
        if app.get_webview_window(&label).is_none() {
            return label;
        }
    }
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!("{}_pop_{}", platform, ts)
}

/// 站外链接用系统默认浏览器打开（仅 Windows：本项目依赖 WebView2）
fn open_in_system_browser(url: &tauri::Url) {
    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new("rundll32")
            .args(["url.dll,FileProtocolHandler", url.as_str()])
            .spawn();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = url;
    }
}

fn ensure_dir(p: &PathBuf) -> std::io::Result<()> {
    if !p.exists() {
        std::fs::create_dir_all(p)?;
    }
    Ok(())
}

/// 快捷键切换语义：
/// - 窗口未启动 → 启动
/// - 已启动但未聚焦（含被最小化）→ 恢复并聚焦
/// - 已聚焦 → 最小化
pub async fn toggle_platform_window(
    app: tauri::AppHandle,
    platform: String,
) -> Result<String, String> {
    let label = match platform.as_str() {
        "dy" => "dy_main",
        "xhs" => "xhs_main",
        _ => return Err(format!("未知 platform: {}", platform)),
    };

    if let Some(existing) = app.get_webview_window(label) {
        let minimized = existing.is_minimized().unwrap_or(false);
        let focused = existing.is_focused().unwrap_or(false);
        if focused && !minimized {
            existing
                .minimize()
                .map_err(|e| format!("最小化 {} 失败: {}", label, e))?;
            return Ok(format!("{} 已最小化", label));
        }
        if minimized {
            let _ = existing.unminimize();
        }
        let _ = existing.show();
        existing
            .set_focus()
            .map_err(|e| format!("聚焦 {} 失败: {}", label, e))?;
        return Ok(format!("{} 已聚焦", label));
    }

    // 窗口不存在（未启动或被关闭过）→ 走正常创建流程
    open_platform_window(app, platform).await
}

/// 根据 platform 返回对应 CDP 端口
pub fn cdp_port_for(platform: &str) -> Option<u16> {
    match platform {
        "dy" => Some(DY_CDP_PORT),
        "xhs" => Some(XHS_CDP_PORT),
        _ => None,
    }
}
