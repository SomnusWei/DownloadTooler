#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::time::Duration;
use tauri::Emitter;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const BRIDGE_REQ: &str = "==DYC_BRIDGE_REQ==";
const BRIDGE_RES: &str = "==DYC_BRIDGE_RES==";

fn main() {
    // ★ POC-1 关键：设置 WebView2 的 CDP 远程调试端口
    // 等价于 Electron 的 --remote-debugging-port=9357
    std::env::set_var(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--remote-debugging-port=9357",
    );

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![poc3_sidecar_echo])
        .setup(|app| {
            println!("[tauri-poc] 启动完成，WebView2 加载 https://www.douyin.com");
            println!("[tauri-poc] CDP 端口 9357（WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS）");

            // ★ POC-3 自动触发：启动 5 秒后自动运行 sidecar echo 测试
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(5)).await;
                println!("[POC-3] 自动触发 sidecar 行协议测试...");
                match poc3_sidecar_echo_inner(app_handle.clone()).await {
                    Ok(payload) => {
                        println!("[POC-3] ✅✅✅ 通过：sidecar 行协议往返成功");
                        println!("[POC-3] 响应: {}", payload);
                        let _ = app_handle.emit("poc3-result", &payload);
                    }
                    Err(e) => {
                        println!("[POC-3] ❌ 失败: {}", e);
                        let _ = app_handle.emit("poc3-error", e);
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// POC-3 内部实现（可被 command 或 setup 调用）
async fn poc3_sidecar_echo_inner(app: tauri::AppHandle) -> Result<String, String> {
    println!("[POC-3] 准备 spawn echo-sidecar");

    // 注意：sidecar() 只传文件名，不含路径
    let sidecar = app
        .shell()
        .sidecar("echo-sidecar")
        .map_err(|e| format!("sidecar 配置错误: {}", e))?;

    let (mut rx, mut child) = sidecar
        .spawn()
        .map_err(|e| format!("spawn 失败: {}", e))?;

    println!("[POC-3] sidecar 已 spawn，PID={:?}", child.pid());

    // 构造一个 POC 请求
    let req_payload = serde_json::json!({
        "id": "poc3-001",
        "url": "https://www.douyin.com/aweme/v1/web/user/profile/other/?sec_user_id=MS4wLjEAAAAA"
    });
    let req_line = format!("{} {}\n", BRIDGE_REQ, req_payload);

    // 同步写入 stdin
    child
        .write(req_line.as_bytes())
        .map_err(|e| format!("stdin write 失败: {}", e))?;
    println!("[POC-3] 已写入 stdin: {}", req_line.trim_end());

    // 监听 stdout
    let mut got_response: Option<String> = None;
    let timeout = tokio::time::sleep(Duration::from_secs(10));
    tokio::pin!(timeout);

    loop {
        tokio::select! {
            _ = &mut timeout => {
                let _ = child.kill();
                return Err("[POC-3] 超时：10 秒内未收到 sidecar 响应".into());
            }
            event = rx.recv() => {
                match event {
                    Some(CommandEvent::Stdout(bytes)) => {
                        let line = String::from_utf8_lossy(&bytes).to_string();
                        println!("[POC-3] sidecar stdout: {}", line.trim_end());
                        if line.starts_with(BRIDGE_RES) {
                            let payload = line[BRIDGE_RES.len()..].trim().to_string();
                            got_response = Some(payload);
                            break;
                        }
                    }
                    Some(CommandEvent::Stderr(bytes)) => {
                        let line = String::from_utf8_lossy(&bytes).to_string();
                        println!("[POC-3] sidecar stderr: {}", line.trim_end());
                    }
                    Some(CommandEvent::Terminated(payload)) => {
                        return Err(format!("[POC-3] sidecar 已退出 code={:?}", payload));
                    }
                    Some(other) => {
                        println!("[POC-3] 其他事件: {:?}", other);
                    }
                    None => {
                        return Err("[POC-3] sidecar 事件流已关闭".into());
                    }
                }
            }
        }
    }

    let _ = child.kill();

    match got_response {
        Some(payload) => {
            println!("[POC-3] ✅ 收到响应: {}", payload);
            Ok(payload)
        }
        None => Err("[POC-3] 未收到响应".into()),
    }
}

/// Tauri command 包装（供前端调用，POC 验证用 setup 自动触发为主）
#[tauri::command]
async fn poc3_sidecar_echo(app: tauri::AppHandle) -> Result<String, String> {
    poc3_sidecar_echo_inner(app).await
}
