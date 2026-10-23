//! Cookie 本地化模块
//!
//! 功能：
//! - 通过 CDP WebSocket 调 `Network.getAllCookies` 拿全部 Cookie
//! - 检测登录态（Dy: sessionid/sid_guard/sessionid_ss；XHS: web_session）
//! - 导出 cookies.json（Dy 和 XHS 各自格式，兼容 Python sidecar）
//! - 30 秒自动导出 + 关闭时同步导出

use crate::paths;
use crate::windows;
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;
use tokio_tungstenite::{connect_async, tungstenite::Message};

/// CDP 返回的单个 Cookie 结构（与 Electron cookies 字段对齐）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Cookie {
    pub name: String,
    pub value: String,
    #[serde(default)]
    pub domain: String,
    #[serde(default)]
    pub path: Option<String>,
    #[serde(default)]
    pub secure: bool,
    #[serde(default, rename = "httpOnly")]
    pub http_only: bool,
    #[serde(default, rename = "expires")]
    pub expiration_date: Option<f64>,
}

/// CDP /json 端点返回的 target 描述
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TargetInfo {
    id: String,
    #[serde(default, rename = "type")]
    #[allow(dead_code)]
    target_type: String,
    #[serde(default)]
    url: String,
    #[serde(default)]
    web_socket_debugger_url: Option<String>,
}

/// 登录态判定（仅检查 cookie 存在且非空；不验证服务器端是否仍认这个 session）
/// 注意：UserDataFolder 持久化的 cookie 可能已服务器端失效但仍被 CDP 读到，
/// 因此本函数返回 true 不等于"实际登录"。需要真实登录态请调 `verify_login`。
fn is_login(platform: &str, cookies: &[Cookie]) -> bool {
    let keys: &[&str] = match platform {
        "dy" => &["sessionid", "sid_guard", "sessionid_ss"],
        "xhs" => &["web_session"],
        _ => return false,
    };
    let now = chrono::Utc::now().timestamp() as f64;
    cookies.iter().any(|c| {
        if !keys.contains(&c.name.as_str()) || c.value.is_empty() {
            return false;
        }
        // persistent cookie 已过期则不算
        match c.expiration_date {
            Some(exp) => exp > now,
            None => true, // session cookie 视为有效
        }
    })
}

/// 按 platform 过滤目标域名 Cookie
fn filter_by_platform(platform: &str, cookies: &[Cookie]) -> Vec<Cookie> {
    let domain_substr: &str = match platform {
        "dy" => "douyin.com",
        "xhs" => "xiaohongshu.com",
        _ => return cookies.to_vec(),
    };
    cookies
        .iter()
        .filter(|c| c.domain.contains(domain_substr))
        .cloned()
        .collect()
}

/// 按 platform URL 子串定位 target（共享 CDP 端口下区分 dy/xhs 页面）
fn url_substr_for_platform(platform: &str) -> Option<&'static str> {
    match platform {
        "dy" => Some("douyin.com"),
        "xhs" => Some("xiaohongshu.com"),
        _ => None,
    }
}

/// HTTP GET /json 拿 page target id 和 webSocketDebuggerUrl
/// 若指定 platform，则按 URL 子串过滤定位该平台的 page target
/// `url_hint` 非空时优先精确命中该 URL 的 target（区分主窗口与站内新窗口）
async fn fetch_target(
    port: u16,
    platform: Option<&str>,
    url_hint: Option<&str>,
) -> Result<(String, String), String> {
    let url = format!("http://127.0.0.1:{}/json", port);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("HTTP GET /json 失败: {}", e))?;
    let status = resp.status();
    let body = resp
        .text()
        .await
        .map_err(|e| format!("读取 /json 响应失败: {}", e))?;
    eprintln!("[cookies] /json status={} body_len={}", status, body.len());
    let targets: Vec<TargetInfo> = serde_json::from_str(&body)
        .map_err(|e| {
            eprintln!("[cookies] /json 解析失败: {}, body 前 200 字符: {}", e, body.chars().take(200).collect::<String>());
            format!("解析 /json 失败: {}", e)
        })?;
    eprintln!("[cookies] /json 解析得到 {} 个 target", targets.len());
    for t in &targets {
        eprintln!(
            "[cookies] target id={} type={} url={} ws={:?}",
            t.id, t.target_type, t.url, t.web_socket_debugger_url.is_some()
        );
    }
    let url_substr = platform.and_then(url_substr_for_platform);
    let candidates: Vec<&TargetInfo> = targets
        .iter()
        .filter(|t| t.target_type == "page")
        .filter(|t| t.web_socket_debugger_url.is_some())
        .filter(|t| match url_substr {
            // 按 URL 子串过滤定位平台 page
            Some(s) => t.url.contains(s),
            // 未指定 platform → 任意 page
            None => true,
        })
        .collect();

    // 站内新窗口（弹窗）与主窗口共用同一 CDP 端口，/json 里会有多个 page target。
    // ① 有窗口 URL 提示时，优先精确命中该 target（主窗口 / 弹窗各自独立）；
    // ② 否则用首次记住的主窗口 target id；
    // ③ 再兜底第一个候选。
    let hinted = url_hint.and_then(|hint| {
        let hint = hint.trim_end_matches('/');
        candidates
            .iter()
            .find(|t| t.url.trim_end_matches('/') == hint)
            .or_else(|| candidates.iter().find(|t| t.url.starts_with(hint)))
            .copied()
    });
    // 窗口 URL 没命中 target（地址略有差异 / 页面刚跳转）时会回退主窗口，
    // 打印出来便于排查「弹窗里下载到了主窗口页面」这类问题
    if url_hint.is_some() && hinted.is_none() {
        eprintln!(
            "[cookies] 窗口 URL 未命中 target，回退主窗口: hint={:?} candidates={:?}",
            url_hint,
            candidates.iter().map(|t| t.url.as_str()).collect::<Vec<_>>()
        );
    }
    let remembered = platform.and_then(|p| {
        main_targets().lock().ok().and_then(|m| m.get(p).cloned())
    });
    let target = hinted
        .or_else(|| {
            remembered
                .as_ref()
                .and_then(|id| candidates.iter().find(|t| &t.id == id).copied())
        })
        .or_else(|| candidates.first().copied())
        .ok_or_else(|| {
            match platform {
                Some(p) => format!("未找到 {} 的 page target（URL 未匹配 {}）", p, url_substr_for_platform(p).unwrap_or("?")),
                None => "未找到可用 target".to_string(),
            }
        })?;

    // 只有未指定窗口提示时才更新「主窗口」记忆，避免弹窗覆盖主窗口 id
    if url_hint.is_none() {
        if let Some(p) = platform {
            if let Ok(mut m) = main_targets().lock() {
                m.insert(p.to_string(), target.id.clone());
            }
        }
    }

    let ws_url = target
        .web_socket_debugger_url
        .clone()
        .ok_or_else(|| "target 缺少 webSocketDebuggerUrl".to_string())?;
    Ok((target.id.clone(), ws_url))
}

/// 主窗口 target id 记忆表（platform → target id），进程内缓存
fn main_targets() -> &'static std::sync::Mutex<HashMap<String, String>> {
    static M: std::sync::OnceLock<std::sync::Mutex<HashMap<String, String>>> =
        std::sync::OnceLock::new();
    M.get_or_init(|| std::sync::Mutex::new(HashMap::new()))
}

/// 通过 CDP WebSocket 调 Network.getAllCookies（指定 platform 以按 URL 过滤 target）
pub async fn cdp_get_all_cookies(port: u16, platform: &str) -> Result<Vec<Cookie>, String> {
    let (_target_id, ws_url) = fetch_target(port, Some(platform), None).await?;
    let (mut ws, _) = connect_async(&ws_url)
        .await
        .map_err(|e| format!("WebSocket 连接失败: {}", e))?;

    let req = serde_json::json!({
        "id": 1,
        "method": "Network.getAllCookies"
    });
    ws.send(Message::Text(req.to_string()))
        .await
        .map_err(|e| format!("WebSocket 发送失败: {}", e))?;

    let mut attempts = 0;
    while let Some(msg) = ws.next().await {
        attempts += 1;
        if attempts > 20 {
            return Err("CDP 响应超时".into());
        }
        match msg {
            Ok(Message::Text(text)) => {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    if v.get("id").and_then(|i| i.as_u64()) == Some(1) {
                        if let Some(err) = v.get("error") {
                            return Err(format!("CDP 错误: {}", err));
                        }
                        if let Some(cookies) = v
                            .get("result")
                            .and_then(|r| r.get("cookies"))
                            .and_then(|c| c.as_array())
                        {
                            let parsed: Vec<Cookie> = cookies
                                .iter()
                                .filter_map(|c| serde_json::from_value(c.clone()).ok())
                                .collect();
                            return Ok(parsed);
                        }
                    }
                }
            }
            Ok(Message::Close(_)) => return Err("WebSocket 被关闭".into()),
            Err(e) => return Err(format!("WebSocket 读取错误: {}", e)),
            _ => {}
        }
    }
    Err("WebSocket 流关闭".into())
}

/// 登录态检测 command（前端调用）
/// CDP 不可达（窗口未启动）时返回 window_open:false，不抛错，
/// 前端据此区分"窗口未启动"和"未登录"。
#[tauri::command]
pub async fn cookie_status(platform: String) -> Result<Value, String> {
    let port = match windows::cdp_port_for(&platform) {
        Some(p) => p,
        None => return Err(format!("未知 platform: {}", platform)),
    };
    // CDP 不可达 → 窗口未启动
    let all = match cdp_get_all_cookies(port, &platform).await {
        Ok(c) => c,
        Err(e) => {
            return Ok(serde_json::json!({
                "window_open": false,
                "login": false,
                "count": 0,
                "note": "窗口未启动或 CDP 不可达",
                "error": e,
            }));
        }
    };
    let filtered = filter_by_platform(&platform, &all);
    let login = is_login(&platform, &filtered);
    // 输出登录 cookie 的过期信息（调试用）
    let now = chrono::Utc::now().timestamp() as f64;
    let login_keys: &[&str] = match platform.as_str() {
        "dy" => &["sessionid", "sid_guard", "sessionid_ss"],
        "xhs" => &["web_session"],
        _ => &[],
    };
    let login_cookies: Vec<Value> = filtered
        .iter()
        .filter(|c| login_keys.contains(&c.name.as_str()))
        .map(|c| {
            serde_json::json!({
                "name": c.name,
                "expires": c.expiration_date,
                "expired": c.expiration_date.map(|exp| exp < now).unwrap_or(false),
                "is_session": c.expiration_date.is_none(),
            })
        })
        .collect();
    Ok(serde_json::json!({
        "window_open": true,
        "login": login,
        "count": filtered.len(),
        "login_cookies": login_cookies,
        "note": "login=true 仅代表 cookie 存在且未过期；服务器端可能已失效。要确认实际登录态请调 verify_login。",
    }))
}

/// 通过 CDP Runtime.evaluate 在页面执行 JS 检查真实登录态
/// 这是绕过 cookie 误判的唯一可靠方式（cookie 存在 ≠ 服务器认账）
#[tauri::command]
pub async fn verify_login(platform: String) -> Result<Value, String> {
    let port = windows::cdp_port_for(&platform)
        .ok_or_else(|| format!("未知 platform: {}", platform))?;
    let js: &str = match platform.as_str() {
        // XHS：检查 window.__INITIAL_STATE__.user.loggedIn（Vue ref，需取 _value/.value）
        "xhs" => r#"(function(){
            try{
                var s = window.__INITIAL_STATE__;
                if(!s || !s.user) return {logged_in:false, method:'initial_state', reason:'no user state'};
                var loggedIn = s.user.loggedIn;
                // loggedIn 可能是 Vue ref 对象（{__v_isRef:true,_value:true}）或裸 boolean
                var val = false;
                if (loggedIn === true) val = true;
                else if (loggedIn && loggedIn._value === true) val = true;
                else if (loggedIn && loggedIn.value === true) val = true;
                return {
                    logged_in: val,
                    method:'initial_state',
                    raw_type: typeof loggedIn,
                    raw_value: loggedIn === true ? true : (loggedIn && loggedIn._value)
                };
            }catch(e){
                return {logged_in:false, method:'initial_state', error:String(e)};
            }
        })()"#,
        // DY：fetch 一个需要登录的简单 API，看返回 status
        "dy" => r#"(async function(){
            try{
                var r = await fetch('https://www.douyin.com/aweme/v1/web/im/user/info/', {credentials:'include'});
                var txt = '';
                try{ txt = (await r.text()).slice(0,200); }catch(_){}
                return {logged_in: r.ok, method:'fetch', status: r.status, body_head: txt};
            }catch(e){
                return {logged_in:false, method:'fetch', error:String(e)};
            }
        })()"#,
        _ => return Err(format!("未知 platform: {}", platform)),
    };
    let result = cdp_eval_js(port, &platform, js).await?;
    let logged_in = result.get("logged_in").and_then(|v| v.as_bool()).unwrap_or(false);
    Ok(serde_json::json!({
        "platform": platform,
        "logged_in": logged_in,
        "detail": result,
    }))
}

/// 通过 CDP Runtime.evaluate 执行 JS（returnByValue + awaitPromise）
pub(crate) async fn cdp_eval_js(port: u16, platform: &str, js: &str) -> Result<Value, String> {
    cdp_eval_js_in(port, platform, None, js).await
}

/// 同上，但用 `url_hint`（聚焦窗口 URL）锁定具体 page target，
/// 避免主窗口与站内新窗口共用 CDP 端口时读错页面。
pub(crate) async fn cdp_eval_js_in(
    port: u16,
    platform: &str,
    url_hint: Option<&str>,
    js: &str,
) -> Result<Value, String> {
    let (_target_id, ws_url) = fetch_target(port, Some(platform), url_hint).await?;
    let (mut ws, _) = connect_async(&ws_url)
        .await
        .map_err(|e| format!("WebSocket 连接失败: {}", e))?;
    let req = serde_json::json!({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": js,
            "awaitPromise": true,
            "returnByValue": true,
        }
    });
    ws.send(Message::Text(req.to_string()))
        .await
        .map_err(|e| format!("WebSocket 发送失败: {}", e))?;
    let mut attempts = 0;
    while let Some(msg) = ws.next().await {
        attempts += 1;
        if attempts > 30 {
            return Err("CDP Runtime.evaluate 响应超时".into());
        }
        match msg {
            Ok(Message::Text(text)) => {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    if v.get("id").and_then(|i| i.as_u64()) == Some(1) {
                        if let Some(err) = v.get("error") {
                            return Err(format!("CDP 错误: {}", err));
                        }
                        if let Some(details) = v.get("result").and_then(|r| r.get("exceptionDetails")) {
                            return Err(format!("JS 异常: {}", details));
                        }
                        // Runtime.evaluate 返回 {result:{result:{value, type}}}
                        if let Some(value) = v
                            .get("result")
                            .and_then(|r| r.get("result"))
                            .and_then(|r| r.get("value"))
                        {
                            return Ok(value.clone());
                        }
                        return Ok(Value::Null);
                    }
                }
            }
            Ok(Message::Close(_)) => return Err("WebSocket 被关闭".into()),
            Err(e) => return Err(format!("WebSocket 读取错误: {}", e)),
            _ => {}
        }
    }
    Err("WebSocket 流关闭".into())
}

/// 手动保存 Cookie 快照（Tauri command）
#[tauri::command]
pub async fn save_cookies(platform: String) -> Result<Value, String> {
    let port = windows::cdp_port_for(&platform)
        .ok_or_else(|| format!("未知 platform: {}", platform))?;
    let all = cdp_get_all_cookies(port, &platform).await?;
    let filtered = filter_by_platform(&platform, &all);
    let login = is_login(&platform, &filtered);
    let count = filtered.len();
    let file = paths::cookie_file_for(&platform);
    paths::ensure_parent_dir(&file).map_err(|e| format!("创建目录失败: {}", e))?;
    write_cookies_file(&platform, &filtered, &file)?;
    let file_str = file.to_string_lossy().to_string();
    Ok(serde_json::json!({
        "ok": true,
        "count": count,
        "login": login,
        "file": file_str,
    }))
}

/// 同步导出（关闭窗口事件用）
pub fn save_cookies_sync(platform: &str) -> Result<(), String> {
    tauri::async_runtime::block_on(async {
        save_cookies(platform.to_string()).await.map(|_| ())
    })
}

/// 写 cookies.json（按 platform 选择格式）
fn write_cookies_file(platform: &str, cookies: &[Cookie], file: &PathBuf) -> Result<(), String> {
    match platform {
        "dy" => write_dy_format(cookies, file),
        "xhs" => write_xhs_format(cookies, file),
        _ => write_dy_format(cookies, file),
    }
}

/// Dy 格式：{ saved_at, login, cookies: {name:value}, full: [{name,value,domain,path,secure,httpOnly,expirationDate}] }
fn write_dy_format(cookies: &[Cookie], file: &PathBuf) -> Result<(), String> {
    let saved_at = chrono::Local::now()
        .format("%Y/%m/%d %H:%M:%S")
        .to_string();
    let login = is_login("dy", cookies);
    let map: HashMap<String, String> = cookies
        .iter()
        .map(|c| (c.name.clone(), c.value.clone()))
        .collect();
    let full: Vec<Value> = cookies
        .iter()
        .map(|c| {
            serde_json::json!({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path.clone().unwrap_or_else(|| "/".into()),
                "secure": c.secure,
                "httpOnly": c.http_only,
                "expirationDate": c.expiration_date,
            })
        })
        .collect();
    let payload = serde_json::json!({
        "saved_at": saved_at,
        "login": login,
        "cookies": map,
        "full": full,
    });
    let text = serde_json::to_string_pretty(&payload).map_err(|e| format!("序列化失败: {}", e))?;
    std::fs::write(file, text).map_err(|e| format!("写文件失败: {}", e))?;
    Ok(())
}

/// XHS 格式：{ saved_at: ISO8601, cookie_header: "n1=v1; ...", cookies: [{name,value,domain}] }
fn write_xhs_format(cookies: &[Cookie], file: &PathBuf) -> Result<(), String> {
    let saved_at = chrono::Utc::now().to_rfc3339();
    let cookie_header = cookies
        .iter()
        .map(|c| format!("{}={}", c.name, c.value))
        .collect::<Vec<_>>()
        .join("; ");
    let rows: Vec<Value> = cookies
        .iter()
        .map(|c| {
            serde_json::json!({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
            })
        })
        .collect();
    let payload = serde_json::json!({
        "saved_at": saved_at,
        "cookie_header": cookie_header,
        "cookies": rows,
    });
    let text = serde_json::to_string_pretty(&payload).map_err(|e| format!("序列化失败: {}", e))?;
    std::fs::write(file, text).map_err(|e| format!("写文件失败: {}", e))?;
    Ok(())
}

/// 30 秒自动导出定时器
pub fn start_auto_exporter(_app: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(30));
        loop {
            interval.tick().await;
            for platform in &["dy", "xhs"] {
                if let Err(e) = save_cookies(platform.to_string()).await {
                    eprintln!("[cookies] 自动导出 {} 失败: {}", platform, e);
                }
            }
        }
    });
}
