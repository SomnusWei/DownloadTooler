//! DownloadTooler - Tauri 2.0 单一桌面端（抖音 + 小红书）
//!
//! P1 第一阶段：框架 + 登录 Cookie 本地化 + 配置中心 UI
//! - 单应用启动：仅创建 panel_main 控制台
//! - dy_main / xhs_main 通过 `open_platform_window` command 按需启动
//! - Cookie 通过 CDP WebSocket 导出（兼容 Python sidecar 零改动）
//! - 30 秒自动导出 + 关闭时同步导出
//! - 配置中心：启动 / 登录态 / 设置 三标签
//! - 系统托盘：关闭最小化到托盘，右键菜单"显示/退出"
//! - 全局快捷键：默认 dy=Alt+1, xhs=Alt+2（用户可重新绑定）
//! - 下载目录 + 快捷键绑定本地持久化（panel_data/settings.json）
//!
//! P1-2：Python 引擎 sidecar 集成（spawn / 行协议 / 日志透传 / 生命周期）
//! P1-3：page_bridge 抽离 + 聚焦域内下载快捷键（Ctrl+D）+ 下载编排

mod bridge;
mod cookies;
mod download;
mod patches;
mod paths;
mod settings;
mod sidecar;
mod toast;
mod windows;

use std::sync::Mutex;

use tauri::Manager;
use tauri::WindowEvent;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri_plugin_global_shortcut::{Builder as GsBuilder, GlobalShortcutExt, ShortcutState};

/// 当前已注册的「下载快捷键」：(快捷键字符串, 平台)
///
/// 该快捷键**只在平台窗口聚焦时**注册、失焦即注销 —— 语义等价「窗口内生效」，
/// 且不会拦截用户在其他应用按 Ctrl+D（如浏览器加书签）。
static DOWNLOAD_SHORTCUT: Mutex<Option<(String, String)>> = Mutex::new(None);

/// 找出当前处于聚焦状态的平台窗口
fn focused_platform(app: &tauri::AppHandle) -> Option<&'static str> {
    for (label, platform) in [("dy_main", "dy"), ("xhs_main", "xhs")] {
        if let Some(w) = app.get_webview_window(label) {
            if w.is_focused().unwrap_or(false) {
                return Some(platform);
            }
        }
    }
    None
}

/// 注册下载快捷键（绑定到指定平台的 `download_current`）
fn register_download_shortcut(app: &tauri::AppHandle, platform: &str, shortcut: &str) {
    if shortcut.is_empty() {
        return;
    }
    let p = platform.to_string();
    if let Err(e) = app
        .global_shortcut()
        .on_shortcut(shortcut, move |app_handle, _sc, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            println!("[download] 快捷键触发 platform={}", p);
            let app = app_handle.clone();
            let p = p.clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = download::download_current(app, p).await {
                    eprintln!("[download] {}", e);
                }
            });
        })
    {
        eprintln!("[download] 注册下载快捷键 {} 失败: {}", shortcut, e);
        return;
    }
    if let Ok(mut g) = DOWNLOAD_SHORTCUT.lock() {
        *g = Some((shortcut.to_string(), platform.to_string()));
    }
    println!("[download] 已注册下载快捷键 {} → {}", shortcut, platform);
}

/// 注销下载快捷键（仅当当前注册的正是该平台时，避免窗口切换竞态误注销）
fn unregister_download_shortcut(app: &tauri::AppHandle, platform: &str) {
    let taken = DOWNLOAD_SHORTCUT.lock().ok().and_then(|mut g| {
        let hit = g.as_ref().map(|(_, p)| p == platform).unwrap_or(false);
        if hit { g.take() } else { None }
    });
    if let Some((sc, _)) = taken {
        let _ = app.global_shortcut().unregister(sc.as_str());
        println!("[download] 已注销下载快捷键 {}", sc);
    }
}

/// 用户在设置页改了下载快捷键后，按当前聚焦状态重新注册
pub(crate) fn refresh_download_shortcut(app: &tauri::AppHandle) {
    let focused = focused_platform(app).map(|s| s.to_string());
    // 先无条件清空现有注册
    let taken = DOWNLOAD_SHORTCUT.lock().ok().and_then(|mut g| g.take());
    if let Some((sc, _)) = taken {
        let _ = app.global_shortcut().unregister(sc.as_str());
    }
    if let Some(p) = focused {
        let s = settings::load();
        register_download_shortcut(app, &p, &s.download_shortcut);
    }
}

/// 平台窗口聚焦状态变化 → 注册 / 注销下载快捷键
fn on_platform_focus(app: &tauri::AppHandle, platform: &str, focused: bool) {
    if focused {
        let already = DOWNLOAD_SHORTCUT
            .lock()
            .ok()
            .and_then(|g| g.as_ref().map(|(_, p)| p.clone()));
        if already.as_deref() == Some(platform) {
            return; // 已为该平台注册，无需重复
        }
        // 平台切换：先清掉旧的再注册新的
        let taken = DOWNLOAD_SHORTCUT.lock().ok().and_then(|mut g| g.take());
        if let Some((sc, _)) = taken {
            let _ = app.global_shortcut().unregister(sc.as_str());
        }
        let s = settings::load();
        register_download_shortcut(app, platform, &s.download_shortcut);
    } else {
        unregister_download_shortcut(app, platform);
    }
}

/// 注册单个 platform 的全局快捷键 + 绑定特定 handler
/// 不依赖 shortcut.to_string() 字符串匹配（因为 Display 格式与输入不同）
pub(crate) fn register_platform_shortcut(
    app: &tauri::AppHandle,
    platform: &str,
    shortcut: &str,
) -> Result<(), String> {
    if shortcut.is_empty() {
        return Ok(());
    }
    let p = platform.to_string();
    app.global_shortcut()
        .on_shortcut(shortcut, move |app_handle, _shortcut, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            println!("[tauri-app] 快捷键触发 platform={}", p);
            let app = app_handle.clone();
            let p = p.clone();
            tauri::async_runtime::spawn(async move {
                match windows::open_platform_window(app, p).await {
                    Ok(msg) => println!("[tauri-app] 快捷键启动: {}", msg),
                    Err(e) => eprintln!("[tauri-app] 快捷键启动失败: {}", e),
                }
            });
        })
        .map_err(|e| format!("注册快捷键 {} 失败: {}", shortcut, e))?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let gs_plugin = GsBuilder::new().build();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(gs_plugin)
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            cookies::cookie_status,
            cookies::save_cookies,
            cookies::verify_login,
            windows::open_platform_window,
            settings::get_settings,
            settings::set_download_dir,
            settings::set_shortcut,
            settings::set_download_shortcut,
            settings::set_dy_quality,
            download::download_current,
            sidecar::sidecar_spawn,
            sidecar::sidecar_run_json,
            sidecar::sidecar_kill,
            sidecar::sidecar_list,
        ])
        .setup(|app| {
            println!("[tauri-app] 启动完成，仅创建 panel_main 控制台");
            windows::build_all(app.handle())?;
            cookies::start_auto_exporter(app.handle().clone());

            // 补丁接口：打印当前生效的引擎补丁（合并引擎只有一个产物）
            patches::log_active(&["dt-engine"]);

            // 注册全局快捷键（默认 dy=Alt+1, xhs=Alt+2，每个快捷键绑定特定 handler）
            let s = settings::load();
            if let Err(e) = register_platform_shortcut(app.handle(), "dy", &s.shortcuts.dy) {
                eprintln!("[tauri-app] 注册 dy 快捷键失败: {}", e);
            } else if !s.shortcuts.dy.is_empty() {
                println!("[tauri-app] 已注册 dy 快捷键: {}", s.shortcuts.dy);
            }
            if let Err(e) = register_platform_shortcut(app.handle(), "xhs", &s.shortcuts.xhs) {
                eprintln!("[tauri-app] 注册 xhs 快捷键失败: {}", e);
            } else if !s.shortcuts.xhs.is_empty() {
                println!("[tauri-app] 已注册 xhs 快捷键: {}", s.shortcuts.xhs);
            }

            // 创建系统托盘菜单
            let menu = Menu::with_items(
                app,
                &[
                    &MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?,
                    &MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?,
                ],
            )?;

            // 系统托盘
            let icon = app
                .default_window_icon()
                .cloned()
                .ok_or_else(|| "默认窗口图标不可用".to_string())?;
            TrayIconBuilder::with_id("main-tray")
                .icon(icon)
                .tooltip("DownloadTooler 控制面板")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("panel_main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("panel_main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                })
                .build(app)?;

            println!(
                "[tauri-app] panel_main 已就绪；dy_main/xhs_main 等待前端按需启动或快捷键触发"
            );
            Ok(())
        })
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                let label = window.label();
                // panel_main 关闭 → 最小化到托盘
                if label == "panel_main" {
                    println!("[tauri-app] panel_main 关闭请求 → 最小化到托盘");
                    api.prevent_close();
                    let _ = window.hide();
                    return;
                }
                // dy_main / xhs_main 关闭 → 同步导出 cookies
                let platform = match label {
                    "dy_main" => "dy",
                    "xhs_main" => "xhs",
                    _ => return,
                };
                println!(
                    "[tauri-app] 窗口 {} 关闭，同步导出 {} cookies",
                    label, platform
                );
                if let Err(e) = cookies::save_cookies_sync(platform) {
                    eprintln!("[tauri-app] 关闭时导出失败: {}", e);
                }
            }
            WindowEvent::Focused(focused) => {
                // 平台窗口获得焦点 → 注册下载快捷键；失焦 → 注销（窗口内生效语义）
                let platform = match window.label() {
                    "dy_main" => "dy",
                    "xhs_main" => "xhs",
                    _ => return,
                };
                on_platform_focus(window.app_handle(), platform, *focused);
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // 应用退出：清理所有 sidecar 子进程，避免残留孤儿进程
            if let tauri::RunEvent::Exit = event {
                println!("[tauri-app] 退出，清理 sidecar 子进程");
                sidecar::kill_all(app_handle);
            }
        });
}
