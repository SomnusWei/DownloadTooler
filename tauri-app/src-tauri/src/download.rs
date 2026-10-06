//! 下载编排：取当前页 URL → 校验作品页 → 起引擎下载 → 日志/结果
//!
//! 触发方式：平台窗口聚焦时按「下载快捷键」（默认 Ctrl+D），由 lib.rs 中
//! 聚焦域内的全局快捷键 handler 调用 `download_current`。
//!
//! 落盘结构沿用 Electron 版：`下载目录/作者名/标题/` —— 视频 `视频.mp4`，图集 `01.jpg`…

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::Arc;

use serde_json::Value;
use tauri::AppHandle;

use crate::cookies;
use crate::paths;
use crate::settings;
use crate::sidecar;
use crate::toast;
use crate::windows;

/// 在 `url` 中查找 `seg`（如 `/video/`）后紧跟的数字 id
fn digits_after(url: &str, seg: &str) -> Option<String> {
    let idx = url.find(seg)? + seg.len();
    let digits: String = url[idx..]
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    if digits.is_empty() {
        None
    } else {
        Some(digits)
    }
}

/// 抖音路径形式作品页：`/video|/note|/gallery|/slides/<数字id>`
fn dy_path_work(url: &str) -> Option<(&'static str, String)> {
    const SEGS: [&str; 4] = ["/video/", "/note/", "/gallery/", "/slides/"];
    for seg in SEGS {
        if let Some(id) = digits_after(url, seg) {
            return Some((seg, id));
        }
    }
    None
}

/// 抖音作品 id：优先路径形式，其次 modal 弹窗的 `modal_id=<id>`
fn dy_work_id(url: &str) -> Option<String> {
    if let Some((_, id)) = dy_path_work(url) {
        return Some(id);
    }
    let key = "modal_id=";
    let idx = url.find(key)? + key.len();
    let id: String = url[idx..]
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    if id.is_empty() {
        None
    } else {
        Some(id)
    }
}

/// 归一化成引擎可用的抖音作品 URL
///
/// 引擎靠 `/{part}/<id>` 提取 aweme_id（见 `dy_fetch.fetch_detail`），
/// 而抖音前台现在多用 **modal 弹窗**展示作品（URL 只带 `modal_id=<id>`，
/// 路径是 `/user/...` 或 `/`），因此必须补成路径形式；类型交由接口响应判定
/// （`parse_detail` 以 `detail.images` 是否存在区分视频 / 图集）。
fn dy_engine_url(url: &str) -> Option<String> {
    if let Some((seg, id)) = dy_path_work(url) {
        return Some(format!("https://www.douyin.com{}{}", seg, id));
    }
    dy_work_id(url).map(|id| format!("https://www.douyin.com/video/{}", id))
}

/// 小红书笔记页：`/explore/<id>` 或 `/discovery/item/<id>`
///
/// 注意：小红书必须保留**完整 URL（含 `xsec_token` 查询参数）** ——
/// `service.single_note_from_current` 要用它重取笔记页 SSR，缺 token 会解析失败，
/// 这与抖音「只取 id」的逻辑不同。
fn is_xhs_note(url: &str) -> bool {
    url.contains("/explore/") || url.contains("/discovery/item/")
}

/// 读取平台窗口当前页地址
async fn current_url(platform: &str) -> Result<String, String> {
    let port = windows::cdp_port_for(platform)
        .ok_or_else(|| format!("未知 platform: {}", platform))?;
    let v = cookies::cdp_eval_js(port, platform, "location.href")
        .await
        .map_err(|e| format!("读取当前页地址失败（窗口未打开或 CDP 不可达）: {}", e))?;
    Ok(v.as_str().unwrap_or("").to_string())
}

/// 抖音「当前作品」解析 JS —— 移植自 Electron `preload.js` 的 `resolveAt` / `digFrom`
///
/// 抖音前台大量使用 modal 弹窗，URL 里往往只有 `modal_id`，因此以**视口中心 DOM 探测**
/// 为主：取中心点元素 → 上溯最多 14 层，命中 `a[href]` 的作品路径或 `data-*` 里的
/// 长数字 id 即返回规范短链；再兜底 `location.pathname` / `location.href`(modal_id)。
/// 返回值为字符串（'' 表示未识别）。
const DY_RESOLVE_JS: &str = r#"(function(){
  var DY_RE = /\/(video|note|gallery|slides)\/(\d+)/i;
  var normUrl = function(t, id){ return 'https://www.douyin.com/' + t + '/' + id; };
  function digFrom(el){
    if(!el) return '';
    var n = el;
    for(var i = 0; n && i < 14; i++, n = n.parentElement){
      try{
        var a = n.tagName === 'A' ? n : (n.closest ? n.closest('a[href]') : null);
        if(a && a.href){ var m = String(a.href).match(DY_RE); if(m) return normUrl(m[1], m[2]); }
        var attrs = n.attributes || [];
        for(var k = 0; k < attrs.length; k++){
          if(/^data-/.test(attrs[k].name)){
            var dm = String(attrs[k].value || '').match(/\b\d{15,}\b/);
            if(dm) return normUrl('video', dm[0]);
          }
        }
      }catch(e){}
    }
    return '';
  }
  function resolveAt(x, y){
    var el = null;
    try{ el = document.elementFromPoint(x, y); }catch(e){}
    var fromEl = digFrom(el);
    if(fromEl) return fromEl;
    var pm = location.pathname.match(DY_RE);
    if(pm) return normUrl(pm[1], pm[2]);
    if(el){
      try{
        var v = el.tagName === 'VIDEO' ? el : (el.closest ? el.closest('video') : null);
        var cand = v || (function(){
          var vs = document.querySelectorAll('video');
          for(var i = 0; i < vs.length; i++){
            var r = vs[i].getBoundingClientRect();
            if(x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return vs[i];
          }
          return null;
        })();
        if(cand){ var d = digFrom(cand); if(d) return d; }
      }catch(e){}
    }
    return '';
  }
  var url = resolveAt(Math.floor(window.innerWidth / 2), Math.floor(window.innerHeight / 2)) || '';
  if(!url){
    var m = location.href.match(DY_RE);
    if(m) url = normUrl(m[1], m[2]);
  }
  if(!url){
    var mm = location.href.match(/[?&]modal_id=(\d+)/);
    if(mm) url = 'https://www.douyin.com/video/' + mm[1];
  }
  return url;
})()"#;

/// 解析抖音图集的文件级进度行：`  图片 3/9: 01.jpg (123456 字节)` → `(3, 9)`
///
/// 图集不做百分比（与小红书保持一致的用户体验），按「图 N/总数」推进。
fn parse_dy_image_progress(line: &str) -> Option<(u32, u32)> {
    let rest = line.trim().strip_prefix("图片 ")?;
    let (cur, rest) = rest.split_once('/')?;
    let total: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
    Some((cur.trim().parse().ok()?, total.parse().ok()?))
}

/// 抖音下载：`dyc-download --url <作品> --dir <下载目录> --cookies-file <cookies.json> [--quality <档位>]`
///
/// 下载过程中把状态推送到 dy_main 右下角 toast（开始 / 进度 / 完成 / 失败）。
async fn download_dy(
    app: &AppHandle,
    port: u16,
    engine_url: &str,
    origin_url: &str,
) -> Result<Value, String> {
    let s = settings::load();
    let dir = settings::download_dir();
    let _ = std::fs::create_dir_all(&dir);
    let cookie_file = paths::dy_cookie_file();

    let mut args: Vec<String> = vec![
        "--url".into(),
        engine_url.to_string(),
        "--dir".into(),
        dir.to_string_lossy().to_string(),
        "--cookies-file".into(),
        cookie_file.to_string_lossy().to_string(),
    ];
    if !s.dy_quality.is_empty() {
        args.push("--quality".into());
        args.push(s.dy_quality.clone());
    }

    let quality_txt = if s.dy_quality.is_empty() {
        "最高".to_string()
    } else {
        format!("{}P", s.dy_quality)
    };
    sidecar::emit_panel_log(
        app,
        "dy",
        format!(
            "开始下载：{}（清晰度 {}）\n          来源页：{}",
            engine_url, quality_txt, origin_url
        ),
    );
    toast::push(port, "dy", "start", &format!("开始下载（{}）", quality_txt)).await;

    // 进度回调：
    // - 图集：按「图片 N/总数」文件级推进（与小红书体验一致），不再报百分比
    // - 视频：按引擎 `[prog] <百分比>` 输出，≥5% 才推送，避免大量 CDP 往返
    let is_gallery = Arc::new(AtomicBool::new(false));
    let last = Arc::new(AtomicI64::new(-100));
    let gallery_cb = is_gallery.clone();
    let last_cb = last.clone();
    let on_line = move |line: &str| {
        if let Some((cur, total)) = parse_dy_image_progress(line) {
            if line.contains("失败") {
                return;
            }
            gallery_cb.store(true, Ordering::Relaxed);
            let text = format!("下载中：图 {}/{}", cur, total);
            tauri::async_runtime::spawn(async move {
                toast::push(port, "dy", "progress", &text).await;
            });
            return;
        }
        let Some(rest) = line.strip_prefix("[prog] ") else {
            return;
        };
        if gallery_cb.load(Ordering::Relaxed) {
            return; // 图集已改走文件级进度
        }
        let Ok(pct) = rest.trim().parse::<i64>() else {
            return;
        };
        if pct - last_cb.load(Ordering::Relaxed) < 5 {
            return;
        }
        last_cb.store(pct, Ordering::Relaxed);
        tauri::async_runtime::spawn(async move {
            toast::push(port, "dy", "progress", &format!("下载中 {}%", pct)).await;
        });
    };
    let cb: &(dyn Fn(&str) + Send + Sync) = &on_line;

    let res = sidecar::run_json(app, "dy-download", &args, None, None, Some(cb)).await;
    match &res {
        Ok(v) => {
            let ok = v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false);
            if ok {
                let kind = v.get("type").and_then(|t| t.as_str()).unwrap_or("video");
                let kind_txt = if kind == "images" { "图集" } else { "视频" };
                let folder = v.get("folder").and_then(|f| f.as_str()).unwrap_or("");
                sidecar::emit_panel_log(app, "dy", format!("下载完成（{}）→ {}", kind, folder));
                toast::push(port, "dy", "done", &format!("下载完成（{}）", kind_txt)).await;
            } else {
                let why = v
                    .get("err")
                    .and_then(|e| e.as_str())
                    .unwrap_or("未知原因");
                sidecar::emit_panel_log(app, "dy", format!("下载失败：{}", v));
                toast::push(port, "dy", "failed", &format!("下载失败：{}", why)).await;
            }
        }
        Err(e) => {
            sidecar::emit_panel_log(app, "dy", format!("下载出错：{}", e));
            toast::push(port, "dy", "failed", &format!("下载失败：{}", e)).await;
        }
    }
    res
}

/// 抖音：页面 DOM 探测当前作品 → 归一化 → 起引擎
async fn download_current_dy(app: &AppHandle) -> Result<Value, String> {
    let port = windows::cdp_port_for("dy")
        .ok_or_else(|| "dy CDP 端口未配置".to_string())?;

    // 与 Electron 一致：优先页面侧探测（视口中心 → 元素上溯），失败再退回当前 URL
    let resolved = cookies::cdp_eval_js(port, "dy", DY_RESOLVE_JS)
        .await
        .map_err(|e| format!("读取抖音页面失败（窗口未打开或 CDP 不可达）: {}", e))?;
    let resolved = resolved.as_str().unwrap_or("").trim().to_string();

    let Some(engine_url) = dy_engine_url(&resolved) else {
        let cur = cookies::cdp_eval_js(port, "dy", "location.href")
            .await
            .ok()
            .and_then(|v| v.as_str().map(|s| s.to_string()))
            .unwrap_or_default();
        let msg = format!(
            "未能识别当前抖音作品，已忽略（页面探测为空，当前页 {}）\
             ——请在作品详情页、或把作品卡片/视频停在窗口中央后重试",
            cur
        );
        sidecar::emit_panel_log(app, "dy", &msg);
        return Err(msg);
    };

    let id = engine_url.rsplit('/').next().unwrap_or("").to_string();
    toast::push(port, "dy", "queued", &format!("已加入下载：{}", id)).await;

    download_dy(app, port, &engine_url, &resolved).await
}

/// 小红书：当前页 URL（须保留 xsec_token）→ `xhs-note` 解析 → `xhs-download` 下载
///
/// 与抖音的差异：
/// - 笔记地址必须**原样保留查询串**（`xsec_token` 是解析 SSR 的必需参数）
/// - 解析与下载是两个引擎：`note_one.py` 挂 CDP 9361 解析当前笔记，
///   `download_one.py` 纯 HTTP + 本地 `cookies.json` 下载
/// - 落盘结构为 `下载目录/作者/标题_<noteId>/`（沿用 XHS 原子项目）
async fn download_current_xhs(app: &AppHandle) -> Result<Value, String> {
    let port = windows::cdp_port_for("xhs")
        .ok_or_else(|| "xhs CDP 端口未配置".to_string())?;

    let url = current_url("xhs").await?;
    if !is_xhs_note(&url) {
        let msg = format!("当前页不是小红书笔记页，已忽略：{}", url);
        sidecar::emit_panel_log(app, "xhs", &msg);
        return Err(msg);
    }

    // ① 解析当前笔记（note_one 通过 CDP 9361 挂接 xhs_main）
    sidecar::emit_panel_log(app, "xhs", format!("解析当前笔记：{}", url));
    let note_args = vec!["--href".to_string(), url.clone()];
    let note_res = sidecar::run_json(app, "xhs-note", &note_args, None, None, None).await?;
    if note_res.get("ok").and_then(|b| b.as_bool()) != Some(true) {
        let why = note_res
            .get("err")
            .and_then(|e| e.as_str())
            .unwrap_or("未知原因");
        let msg = format!("解析笔记失败：{}", why);
        sidecar::emit_panel_log(app, "xhs", &msg);
        toast::push(port, "xhs", "failed", &format!("下载失败：{}", why)).await;
        return Err(msg);
    }

    let note_id = note_res
        .get("note")
        .and_then(|n| n.get("note_id"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let title = note_res
        .get("note")
        .and_then(|n| n.get("title"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let id_txt = if note_id.is_empty() { "笔记".to_string() } else { note_id.clone() };
    toast::push(port, "xhs", "queued", &format!("已加入下载：{}", id_txt)).await;

    // ② 写临时 note-json（download_one 的入参格式：{meta, note}）
    let tmp = std::env::temp_dir().join(format!("dt-xhs-note-{}.json", id_txt));
    let payload = serde_json::json!({
        "meta": note_res.get("meta").cloned().unwrap_or(Value::Null),
        "note": note_res.get("note").cloned().unwrap_or(Value::Null),
    });
    std::fs::write(&tmp, payload.to_string()).map_err(|e| format!("写临时文件失败: {}", e))?;

    let dir = settings::download_dir();
    let _ = std::fs::create_dir_all(&dir);
    let dl_args = vec![
        "--note-json".to_string(),
        tmp.to_string_lossy().to_string(),
        "--dir".to_string(),
        dir.to_string_lossy().to_string(),
    ];

    let title_txt = if title.is_empty() { id_txt.clone() } else { title.clone() };
    sidecar::emit_panel_log(
        app,
        "xhs",
        format!("开始下载：{}（最高清晰度）", title_txt),
    );
    toast::push(port, "xhs", "start", "开始下载（最高清晰度）").await;

    // ③ 下载（纯 HTTP + 本地 cookies.json）
    //
    // XHS 引擎没有百分比输出，只有文件级进度行 `[<note_id>] <文件显示名>`
    // （如 `[abc] 图 3` / `[abc] 视频`），据此推送「下载中：…」进度 toast。
    let prefix = format!("[{}] ", note_id);
    let on_line = move |line: &str| {
        let Some(disp) = line.strip_prefix(prefix.as_str()) else {
            return;
        };
        let disp = disp.trim();
        if disp.is_empty() || disp.contains("失败") || disp.contains("跳过") {
            return;
        }
        let text = format!("下载中：{}", disp);
        tauri::async_runtime::spawn(async move {
            toast::push(port, "xhs", "progress", &text).await;
        });
    };
    let cb: &(dyn Fn(&str) + Send + Sync) = &on_line;

    let res = sidecar::run_json(app, "xhs-download", &dl_args, None, None, Some(cb)).await;
    let _ = std::fs::remove_file(&tmp);

    match &res {
        Ok(v) => {
            let ok = v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false);
            if ok {
                let state = v.get("state").and_then(|s| s.as_str()).unwrap_or("");
                sidecar::emit_panel_log(
                    app,
                    "xhs",
                    format!("下载完成（{}）→ {}", state, dir.display()),
                );
                toast::push(port, "xhs", "done", "下载完成（笔记）").await;
            } else {
                let why = v
                    .get("msg")
                    .or_else(|| v.get("err"))
                    .and_then(|e| e.as_str())
                    .unwrap_or("未知原因");
                sidecar::emit_panel_log(app, "xhs", format!("下载失败：{}", v));
                toast::push(port, "xhs", "failed", &format!("下载失败：{}", why)).await;
            }
        }
        Err(e) => {
            sidecar::emit_panel_log(app, "xhs", format!("下载出错：{}", e));
            toast::push(port, "xhs", "failed", &format!("下载失败：{}", e)).await;
        }
    }
    res
}

/// 下载当前窗口显示的作品 / 笔记（快捷键触发）
///
/// 返回引擎结果 JSON；同时把日志打到【日志】标签。
#[tauri::command]
pub async fn download_current(app: AppHandle, platform: String) -> Result<Value, String> {
    match platform.as_str() {
        "dy" => download_current_dy(&app).await,
        "xhs" => download_current_xhs(&app).await,
        _ => Err(format!("未知 platform: {}", platform)),
    }
}

#[cfg(test)]
mod tests {
    use super::{dy_engine_url, is_xhs_note, parse_dy_image_progress};

    #[test]
    fn dy_modal_url_normalized() {
        // 抖音前台 modal 弹窗形式：路径是 /user/...，作品 id 在 modal_id
        let u = "https://www.douyin.com/user/self?from_tab_name=main\
                 &modal_id=7686381947374891634&showTab=like";
        assert_eq!(
            dy_engine_url(u).as_deref(),
            Some("https://www.douyin.com/video/7686381947374891634")
        );
    }

    #[test]
    fn dy_path_url_keeps_kind_and_drops_query() {
        assert_eq!(
            dy_engine_url("https://www.douyin.com/note/123456").as_deref(),
            Some("https://www.douyin.com/note/123456")
        );
        assert_eq!(
            dy_engine_url("https://www.douyin.com/video/999?foo=1").as_deref(),
            Some("https://www.douyin.com/video/999")
        );
    }

    #[test]
    fn dy_home_is_not_work() {
        assert!(dy_engine_url("https://www.douyin.com/?recommend=1").is_none());
    }

    #[test]
    fn xhs_note_detection() {
        assert!(is_xhs_note(
            "https://www.xiaohongshu.com/explore/abc123?xsec_token=XY&xsec_source=pc_feed"
        ));
        assert!(is_xhs_note("https://www.xiaohongshu.com/discovery/item/abc"));
        assert!(!is_xhs_note("https://www.xiaohongshu.com/explore"));
    }

    #[test]
    fn dy_image_progress_line() {
        // 引擎图集进度行（注意前导空格）
        assert_eq!(
            parse_dy_image_progress("  图片 3/9: 03.jpg (123456 字节)"),
            Some((3, 9))
        );
        // 失败行同样能取出进度
        assert_eq!(
            parse_dy_image_progress("  图片 4/9 失败: timeout"),
            Some((4, 9))
        );
        // 非进度行不应误匹配
        assert_eq!(parse_dy_image_progress("[prog] 42"), None);
        assert_eq!(parse_dy_image_progress("[dl] 图集 · 作者 · 123 · 9 张"), None);
    }
}
