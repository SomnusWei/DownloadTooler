//! sidecar 集成：Python 引擎 spawn + 行协议 + 日志透传 + 生命周期
//!
//! 引擎形态：**多入口合并的 onedir 引擎** `engines/dt-engine/dt-engine.exe`
//! （三个任务共用一份 Python 运行时与依赖，免去 onefile 每次启动解包），
//! 具体任务由环境变量 `DT_TASK` 选择（`dy-download` / `xhs-note` / `xhs-download`）。
//!
//! 职责：
//! - 引擎可执行文件解析：补丁目录 → 开发期 `src-tauri/engines` → 生产期 resource_dir
//! - `CommandEvent::Stdout` 流式解析行协议：`==DYC_JSON==` / `==XHS_JSON==`
//! - page_bridge 双向往返：收到 `==DYC_BRIDGE_REQ==` → 交给 `bridge::exec` 在
//!   dy_main 内同源 fetch → `child.write()` 回写 `==DYC_BRIDGE_RES==`
//! - 日志透传：`app.emit("sidecar-log", ...)`，前端 `listen('sidecar-log')` 展示
//! - 生命周期：`sidecar_kill` 主动结束；应用退出时 `kill_all` 清理子进程
//! - 尾部滑动窗口：stdout 累计上限（下载 64KB / 结果 1MB）防内存失控
//!
//! 环境变量传递：Dy 任务注入 `DYC_BRIDGE=1`，XHS 任务注入 `XHS_ROOT`（定位 cookies.json）。

use std::collections::HashMap;
#[cfg(debug_assertions)]
use std::path::Path;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::bridge::{BRIDGE_REQ, BRIDGE_RES};
use crate::paths;

/// 合并后的多入口引擎名（onedir：`engines/<ENGINE>/<ENGINE>.exe`）
const ENGINE: &str = "dt-engine";

/// 结果 JSON 的标记行（与 Python 引擎 / Electron 版完全一致）
pub const DYC_JSON: &str = "==DYC_JSON==";
pub const XHS_JSON: &str = "==XHS_JSON==";

/// stdout 逐行回调（下载进度解析等）。同步闭包内如需异步操作请自行 spawn。
pub type LineCallback<'a> = &'a (dyn Fn(&str) + Send + Sync);

/// stdout 累计上限：抓取结果（作品清单可能很大）保留 1MB
const TAIL_COLLECT: usize = 1024 * 1024;
/// stdout 累计上限：下载 / 长驻流式场景保留 64KB
const TAIL_STREAM: usize = 64 * 1024;

/// 长驻 sidecar 句柄表（run_id → 子进程 PID）
#[derive(Default)]
pub struct SidecarState {
    running: Mutex<HashMap<String, u32>>,
    seq: AtomicU64,
}

/// 结束进程（Windows：taskkill /T 连带子进程；CREATE_NO_WINDOW 避免弹窗）
fn terminate(pid: u32) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let out = std::process::Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| format!("taskkill 调用失败: {}", e))?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = pid;
        Err("非 Windows 平台未实现".into())
    }
}

/// 透传前端的日志事件负载
#[derive(Clone, Serialize)]
struct LogPayload {
    run_id: String,
    engine: String,
    stream: String,
    line: String,
}

/// 透传前端的结束事件负载
#[derive(Clone, Serialize)]
struct ExitPayload {
    run_id: String,
    engine: String,
    code: Option<i32>,
}

/// 根据任务推导结果 JSON 的标记行
fn json_marker(task: &str) -> &'static str {
    if task.starts_with("xhs") {
        XHS_JSON
    } else {
        DYC_JSON
    }
}

/// 解析引擎可执行文件：补丁目录 → 开发期 `src-tauri/engines` → 生产期 resource_dir
fn engine_exe(app: &AppHandle, engine: &str) -> Result<PathBuf, String> {
    if let Some(p) = crate::patches::engine_override(engine) {
        return Ok(p);
    }
    let rel = PathBuf::from("engines")
        .join(engine)
        .join(format!("{}.exe", engine));

    #[cfg(debug_assertions)]
    {
        let p = Path::new(env!("CARGO_MANIFEST_DIR")).join(&rel);
        if p.is_file() {
            return Ok(p);
        }
    }

    let dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir 不可用: {}", e))?;
    let p = dir.join(&rel);
    if p.is_file() {
        Ok(p)
    } else {
        Err(format!("引擎未找到: {}（请先运行 tools/build_sidecars.ps1）", p.display()))
    }
}

/// 构造引擎命令
///
/// 任务通过 `DT_TASK` 环境变量下发（多入口合并引擎按它分发）；
/// Dy 任务需要 page_bridge（`DYC_BRIDGE=1`），XHS 任务需要 `XHS_ROOT` 定位 cookies.json。
fn build_command(
    app: &AppHandle,
    task: &str,
    args: &[String],
    cwd: Option<&str>,
    env: Option<&HashMap<String, String>>,
) -> Result<tauri_plugin_shell::process::Command, String> {
    let exe = engine_exe(app, ENGINE)?;
    let cmd = app
        .shell()
        .command(exe.to_string_lossy().to_string())
        .args(args);

    // 默认 cwd / 环境
    let mut cmd = if task.starts_with("dy") {
        let dy_dir = paths::dy_cookie_file()
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| paths::dy_user_data_dir());
        let _ = std::fs::create_dir_all(&dy_dir);
        cmd.env("DYC_BRIDGE", "1")
            .env("PYTHONUTF8", "1")
            .current_dir(cwd.map(String::from).unwrap_or_else(|| dy_dir.to_string_lossy().to_string()))
    } else if task.starts_with("xhs") {
        // XHS 任务用 `XHS_ROOT/cookies.json` 读写 Cookie（download 依赖其中的 cookie_header）
        let xhs_dir = paths::xhs_cookie_file()
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| paths::xhs_user_data_dir());
        let _ = std::fs::create_dir_all(&xhs_dir);
        cmd.env("XHS_ROOT", xhs_dir.to_string_lossy().to_string())
            .env("PYTHONUTF8", "1")
            .current_dir(cwd.map(String::from).unwrap_or_else(|| xhs_dir.to_string_lossy().to_string()))
    } else {
        match cwd {
            Some(c) => cmd.current_dir(c),
            None => cmd,
        }
    };
    cmd = cmd.env("DT_TASK", task);

    if let Some(extra) = env {
        for (k, v) in extra {
            cmd = cmd.env(k, v);
        }
    }
    Ok(cmd)
}

/// 向【日志】标签推送一条面板级日志（下载流程 / 校验提示等）
pub fn emit_panel_log(app: &AppHandle, engine: &str, line: impl Into<String>) {
    let _ = app.emit(
        "sidecar-log",
        LogPayload {
            run_id: "panel".into(),
            engine: engine.to_string(),
            stream: "panel".into(),
            line: line.into(),
        },
    );
}

/// 向子进程 stdin 回写一行（行协议应答）
fn write_line(child: &Arc<Mutex<CommandChild>>, line: &str) -> Result<(), String> {
    let mut c = child.lock().map_err(|_| "sidecar 句柄锁定失败".to_string())?;
    c.write(line.as_bytes())
        .map_err(|e| format!("stdin 写入失败: {}", e))
}

/// 从累计输出中提取结果 JSON
///
/// 注意两种形态：
/// 1) 成功：`…\n==DYC_JSON==\n{json}` —— 但 Windows 下 Python stdout 会把 `\n`
///    翻译成 `\r\n`，故**不能**用 `"\n<marker>\n"` 精确匹配，只能 `rfind(marker)` 后 trim；
/// 2) 失败：引擎的部分失败分支只打印**裸 JSON**（不带标记），此时回退解析末尾的 JSON 对象。
fn extract_json(tail: &str, marker: &str) -> Option<Value> {
    if let Some(idx) = tail.rfind(marker) {
        let text = &tail[idx + marker.len()..];
        if let Ok(v) = serde_json::from_str::<Value>(text.trim()) {
            return Some(v);
        }
    }
    // 兜底：末尾的裸 JSON（失败分支）
    let start = tail.rfind('{')?;
    serde_json::from_str::<Value>(tail[start..].trim()).ok()
}

/// 解码引擎输出：优先 UTF-8，失败回退 GBK
///
/// PyInstaller 冻结后 stdio 可能回落到 GBK（PYTHONUTF8 不一定生效），
/// 如 `dyc_service.py` 未像 `dyc_download.py` 那样显式 reconfigure，
/// 其中文日志会是 GBK 字节；这里做兜底解码，避免日志乱码。
fn decode_output(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => encoding_rs::GBK.decode(bytes).0.into_owned(),
    }
}

/// 运行一个 sidecar 并等待其结束，返回结束码与累计输出（尾部窗口内）
///
/// 按「字节」缓冲再切行，避免多字节字符跨 chunk 被截断导致解码错误。
async fn drain_to_end(
    app: &AppHandle,
    run_id: &str,
    engine: &str,
    limit: usize,
    mut rx: tauri::async_runtime::Receiver<CommandEvent>,
    child: Arc<Mutex<CommandChild>>,
    on_line: Option<LineCallback<'_>>,
) -> (Option<i32>, String) {
    let mut tail: Vec<u8> = Vec::new();
    let mut pending: Vec<u8> = Vec::new();
    let mut code: Option<i32> = None;

    loop {
        let Some(ev) = rx.recv().await else { break };
        match ev {
            CommandEvent::Stdout(bytes) => {
                tail.extend_from_slice(&bytes);
                if tail.len() > limit {
                    let cut = tail.len() - limit;
                    tail.drain(..cut);
                }
                pending.extend_from_slice(&bytes);
                while let Some(pos) = pending.iter().position(|&b| b == b'\n') {
                    let raw: Vec<u8> = pending.drain(..=pos).collect();
                    let line = decode_output(&raw).trim_end().to_string();
                    if line.is_empty() {
                        continue;
                    }
                    if line.starts_with(BRIDGE_REQ) {
                        let payload = line[BRIDGE_REQ.len()..].trim().to_string();
                        if let Ok(req) = serde_json::from_str::<Value>(&payload) {
                            let res = crate::bridge::exec(&req).await;
                            let line_out = format!("{} {}\n", BRIDGE_RES, res);
                            if let Err(e) = write_line(&child, &line_out) {
                                eprintln!("[sidecar] bridge 回写失败: {}", e);
                            }
                        }
                    } else {
                        if let Some(cb) = on_line {
                            cb(&line);
                        }
                        let _ = app.emit(
                            "sidecar-log",
                            LogPayload {
                                run_id: run_id.to_string(),
                                engine: engine.to_string(),
                                stream: "stdout".into(),
                                line,
                            },
                        );
                    }
                }
            }
            CommandEvent::Stderr(bytes) => {
                let text = decode_output(&bytes);
                for line in text.lines() {
                    if line.trim().is_empty() {
                        continue;
                    }
                    let _ = app.emit(
                        "sidecar-log",
                        LogPayload {
                            run_id: run_id.to_string(),
                            engine: engine.to_string(),
                            stream: "stderr".into(),
                            line: line.to_string(),
                        },
                    );
                }
            }
            CommandEvent::Error(e) => {
                let _ = app.emit(
                    "sidecar-log",
                    LogPayload {
                        run_id: run_id.to_string(),
                        engine: engine.to_string(),
                        stream: "error".into(),
                        line: e,
                    },
                );
            }
            CommandEvent::Terminated(payload) => {
                code = payload.code;
                break;
            }
            _ => {}
        }
    }

    // 处理残留未换行的尾部内容
    let rest = decode_output(&pending);
    let rest = rest.trim();
    if !rest.is_empty() && !rest.starts_with(BRIDGE_REQ) {
        let _ = app.emit(
            "sidecar-log",
            LogPayload {
                run_id: run_id.to_string(),
                engine: engine.to_string(),
                stream: "stdout".into(),
                line: rest.to_string(),
            },
        );
    }

    let _ = app.emit(
        "sidecar-exit",
        ExitPayload {
            run_id: run_id.to_string(),
            engine: engine.to_string(),
            code,
        },
    );
    (code, decode_output(&tail))
}

/// 启动长驻 sidecar（流式日志 + page_bridge 应答），返回 run_id
#[tauri::command]
pub async fn sidecar_spawn(
    app: AppHandle,
    task: String,
    args: Option<Vec<String>>,
    cwd: Option<String>,
    env: Option<HashMap<String, String>>,
) -> Result<String, String> {
    let state = app.state::<SidecarState>();
    let run_id = format!("{}-{}", task, state.seq.fetch_add(1, Ordering::SeqCst) + 1);

    let cmd = build_command(&app, &task, args.as_deref().unwrap_or(&[]), cwd.as_deref(), env.as_ref())?;
    let (rx, child) = cmd.spawn().map_err(|e| format!("spawn {} 失败: {}", task, e))?;

    let pid = child.pid();
    let child = Arc::new(Mutex::new(child));
    state
        .running
        .lock()
        .map_err(|_| "sidecar 状态锁定失败".to_string())?
        .insert(run_id.clone(), pid);

    println!("[sidecar] 启动 {} (task={}, pid={})", run_id, task, pid);

    let app2 = app.clone();
    let rid = run_id.clone();
    let eng = task.clone();
    tauri::async_runtime::spawn(async move {
        let (_code, _tail) = drain_to_end(&app2, &rid, &eng, TAIL_STREAM, rx, child, None).await;
        if let Some(state) = app2.try_state::<SidecarState>() {
            if let Ok(mut m) = state.running.lock() {
                m.remove(&rid);
            }
        }
    });

    Ok(run_id)
}

/// 运行 sidecar 并等待结束，返回结果 JSON（内部复用：下载编排等）
///
/// 日志仍通过 `sidecar-log` 事件实时透传到【日志】标签。
pub(crate) async fn run_json(
    app: &AppHandle,
    task: &str,
    args: &[String],
    cwd: Option<&str>,
    env: Option<&HashMap<String, String>>,
    on_line: Option<LineCallback<'_>>,
) -> Result<Value, String> {
    let state = app.state::<SidecarState>();
    let run_id = format!("{}-{}", task, state.seq.fetch_add(1, Ordering::SeqCst) + 1);
    let marker = json_marker(task);
    println!("[sidecar] run_json 启动 {} (task={})", run_id, task);

    let cmd = build_command(app, task, args, cwd, env)?;
    let (rx, child) = cmd.spawn().map_err(|e| format!("spawn {} 失败: {}", task, e))?;
    let child = Arc::new(Mutex::new(child));

    let (_code, tail) =
        drain_to_end(app, &run_id, task, TAIL_COLLECT, rx, child, on_line).await;

    extract_json(&tail, marker).ok_or_else(|| {
        let head: String = tail.chars().take(400).collect();
        format!("未在 {} 输出中找到结果 JSON（{}），尾部: {}", task, marker, head)
    })
}

/// 运行 sidecar 并捕获结果 JSON（一次性：下载 / 采集），返回 `==DYC_JSON==` / `==XHS_JSON==` 解析结果
#[tauri::command]
pub async fn sidecar_run_json(
    app: AppHandle,
    task: String,
    args: Option<Vec<String>>,
    cwd: Option<String>,
    env: Option<HashMap<String, String>>,
) -> Result<Value, String> {
    run_json(
        &app,
        &task,
        args.as_deref().unwrap_or(&[]),
        cwd.as_deref(),
        env.as_ref(),
        None,
    )
    .await
}

/// 主动结束指定 run_id 的 sidecar
#[tauri::command]
pub async fn sidecar_kill(app: AppHandle, run_id: String) -> Result<bool, String> {
    let state = app.state::<SidecarState>();
    let handle = state
        .running
        .lock()
        .map_err(|_| "sidecar 状态锁定失败".to_string())?
        .remove(&run_id);
    match handle {
        Some(pid) => {
            terminate(pid)?;
            println!("[sidecar] 已结束 {} (pid={})", run_id, pid);
            Ok(true)
        }
        None => Ok(false),
    }
}

/// 结束全部 sidecar（应用退出时调用）
pub fn kill_all(app: &AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    let handles: Vec<(String, u32)> = match state.running.lock() {
        Ok(mut m) => m.drain().collect(),
        Err(_) => return,
    };
    for (rid, pid) in handles {
        match terminate(pid) {
            Ok(_) => println!("[sidecar] 已清理 {} (pid={})", rid, pid),
            Err(e) => eprintln!("[sidecar] 清理 {} 失败: {}", rid, e),
        }
    }
}

/// 当前运行中的 sidecar 列表（前端状态展示）
#[tauri::command]
pub async fn sidecar_list(app: AppHandle) -> Result<Vec<String>, String> {
    let state = app.state::<SidecarState>();
    let map = state
        .running
        .lock()
        .map_err(|_| "sidecar 状态锁定失败".to_string())?;
    Ok(map.keys().cloned().collect())
}

#[cfg(test)]
mod tests {
    use super::{decode_output, extract_json, DYC_JSON};

    #[test]
    fn utf8_passthrough() {
        assert_eq!(decode_output("目标: https://www.douyin.com".as_bytes()),
                   "目标: https://www.douyin.com");
    }

    #[test]
    fn gbk_fallback() {
        // "目标" 的 GBK 字节（PyInstaller 冻结后 stdio 回落 GBK 的场景）
        let gbk = [0xC4u8, 0xBF, 0xB1, 0xEA];
        assert_eq!(decode_output(&gbk), "目标");
    }

    #[test]
    fn extract_json_crlf_ok() {
        // Windows 下 Python stdout 的 \n 会变成 \r\n
        let tail = "日志\r\n\r\n==DYC_JSON==\r\n{\"ok\":true,\"type\":\"video\",\"folder\":\"D:\\\\d\"}\r\n";
        let v = extract_json(tail, DYC_JSON).expect("应能解析 CRLF 形态的结果 JSON");
        assert_eq!(v["ok"], serde_json::json!(true));
        assert_eq!(v["type"], "video");
    }

    #[test]
    fn extract_json_bare_failure_fallback() {
        // 引擎失败分支只打印裸 JSON（不带标记）
        let tail = "日志\r\n{\"ok\":false,\"err\":\"图集无图片地址\"}\r\n";
        let v = extract_json(tail, DYC_JSON).expect("应能兜底解析裸 JSON");
        assert_eq!(v["err"], "图集无图片地址");
    }
}
