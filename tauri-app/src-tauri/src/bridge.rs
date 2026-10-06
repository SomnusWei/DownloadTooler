//! page_bridge：在已登录的 dy_main webview 内执行同源 fetch，绕过抖音 Argus 风控
//!
//! 背景：抖音自 2026-09 起对 `/aweme/v1/web/aweme/detail/` 等接口加 Argus 风控 ——
//! 非页面请求（aiohttp/urllib 直连）即便签名与 Cookie 都正确也会被
//! `403 Blocked by ArgusSecurityPlugin` 拦掉；而在 www.douyin.com 页面内发起的
//! 同源 fetch 则放行。
//!
//! 协议（行式，引擎 stdout / 主进程 stdin）：
//!   引擎   → stdout : `==DYC_BRIDGE_REQ== {"id":1,"url":"...","method":"GET"}`
//!   主进程 → stdin  : `==DYC_BRIDGE_RES== {"id":1,"http_status":200,"text":"..."}`
//!
//! POC-2 已验证：WebView2 内 `fetch(..., {credentials:'include'})` 会自动携带
//! `sessionid` 等 Cookie（等价 Electron 的 `eval_with_callback` 同源通道）。

use serde_json::{json, Value};

use crate::cookies;
use crate::windows;

/// 引擎发起 bridge 请求的前缀
pub const BRIDGE_REQ: &str = "==DYC_BRIDGE_REQ==";
/// 主进程回写 bridge 应答的前缀
pub const BRIDGE_RES: &str = "==DYC_BRIDGE_RES==";

/// 执行一条 bridge 请求，返回给引擎的应答 JSON
///
/// 应答形状：`{id, http_status, text}`；出错时为 `{id, error, http_status?}`
pub async fn exec(req: &Value) -> Value {
    let id = req.get("id").cloned().unwrap_or(Value::Null);
    let url = req.get("url").and_then(|v| v.as_str()).unwrap_or("");
    let method = req
        .get("method")
        .and_then(|v| v.as_str())
        .unwrap_or("GET")
        .to_uppercase();
    let body = req.get("body").and_then(|v| v.as_str()).unwrap_or("");

    // 与 Electron 一致：bridge 仅允许 douyin.com 同源地址
    if !url.starts_with("https://") || !url.contains("douyin.com/") {
        return json!({"id": id, "error": "bridge 仅支持 douyin.com 同源地址"});
    }

    let Some(port) = windows::cdp_port_for("dy") else {
        return json!({"id": id, "error": "dy CDP 端口未配置"});
    };

    let url_lit = serde_json::to_string(url).unwrap_or_else(|_| "\"\"".into());
    let method_lit = serde_json::to_string(&method).unwrap_or_else(|_| "\"GET\"".into());
    let body_lit = serde_json::to_string(body).unwrap_or_else(|_| "\"\"".into());
    let js = format!(
        r#"(async function(){{
            try {{
                var opt = {{method: {method_lit}, credentials: 'include'}};
                {body_part}
                var r = await fetch({url_lit}, opt);
                var t = await r.text();
                return {{http_status: r.status, text: t}};
            }} catch(e) {{
                return {{http_status: 0, text: '', error: String(e)}};
            }}
        }})()"#,
        body_part = if body.is_empty() {
            String::new()
        } else {
            format!("opt.body = {body_lit};")
        }
    );

    match cookies::cdp_eval_js(port, "dy", &js).await {
        Ok(v) => {
            let status = v.get("http_status").and_then(|s| s.as_i64()).unwrap_or(0);
            let text = v.get("text").and_then(|t| t.as_str()).unwrap_or("");
            if let Some(err) = v.get("error").and_then(|e| e.as_str()) {
                return json!({"id": id, "error": err, "http_status": status});
            }
            json!({"id": id, "http_status": status, "text": text})
        }
        Err(e) => json!({"id": id, "error": format!("bridge CDP 执行失败: {}", e)}),
    }
}
