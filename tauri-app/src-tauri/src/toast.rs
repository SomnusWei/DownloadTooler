//! 平台窗口右下角状态提示（toast）
//!
//! dy_main / xhs_main 是**外部站点页面**（douyin.com / xiaohongshu.com），
//! 无法挂 Vue 组件，也没有页面内 IPC 通道；因此通过 CDP `Runtime.evaluate`
//! 在页面内维护一个固定右下角的 toast 宿主：
//!
//! - 渐入渐出（`opacity` + `translateY` 过渡）
//! - 多条并存时向上堆叠，**先出现的在上方**，最多保留 5 条
//! - `progress` 复用同一条并原地更新百分比，不重复堆叠
//! - 结果类（done / failed）出现时清掉进行中的 progress
//!
//! 状态类型（与需求一致）：
//! `queued` 添加下载队列 / `start` 开始下载 / `progress` 下载进度 /
//! `done` 下载完成 / `failed` 下载失败

use serde_json::json;

use crate::cookies;

/// 宿主 JS：幂等定义 `window.__dtToast(kind, text)`
///
/// 页面导航后全局函数会丢失，下次注入时自动重建（host 元素按 id 复用）。
/// 注意：设置样式走 CSSOM（`style.cssText`），不受站点 CSP 的 `style-src` 限制。
const HOST_JS: &str = r#"(function(){
  if(window.__dtToast) return true;
  var HOST = 'dt-toast-host';
  var STYLE = {
    queued:   { bg:'rgba(30,41,59,.95)',  bar:'#60a5fa', icon:'＋' },
    start:    { bg:'rgba(30,41,59,.95)',  bar:'#38bdf8', icon:'↓' },
    progress: { bg:'rgba(30,41,59,.95)',  bar:'#fbbf24', icon:'⋯' },
    done:     { bg:'rgba(20,83,45,.95)',  bar:'#4ade80', icon:'✓' },
    failed:   { bg:'rgba(127,29,29,.95)', bar:'#f87171', icon:'✕' }
  };
  function host(){
    var h = document.getElementById(HOST);
    if(!h){
      h = document.createElement('div');
      h.id = HOST;
      h.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;' +
        'display:flex;flex-direction:column;align-items:flex-end;gap:8px;pointer-events:none;';
      (document.body || document.documentElement).appendChild(h);
    }
    return h;
  }
  function alive(h){
    var out = [];
    for(var i = 0; i < h.children.length; i++){
      if(!h.children[i]._dying) out.push(h.children[i]);
    }
    return out;
  }
  function remove(el){
    if(!el || el._dying) return;
    el._dying = true;
    el.style.opacity = '0';
    el.style.transform = 'translateY(-6px)';
    setTimeout(function(){ try{ el.remove(); }catch(e){} }, 260);
  }
  // 超出上限：优先淘汰最旧的非 progress 项
  function trim(){
    var h = host();
    var list = alive(h);
    while(list.length > 5){
      var target = null;
      for(var i = 0; i < list.length; i++){
        if(list[i].getAttribute('data-kind') !== 'progress'){ target = list[i]; break; }
      }
      target = target || list[0];
      if(!target) break;
      remove(target);
      list = alive(h);
    }
  }
  window.__dtToast = function(kind, text){
    var cfg = STYLE[kind] || STYLE.start;
    var h = host();
    var el = null;
    if(kind === 'progress'){
      var cand = h.querySelector('[data-kind="progress"]');
      if(cand && !cand._dying) el = cand;
    }
    if(!el){
      el = document.createElement('div');
      el.setAttribute('data-kind', kind);
      el.style.cssText = 'pointer-events:none;display:flex;align-items:center;gap:8px;' +
        'min-width:190px;max-width:420px;padding:9px 14px;border-radius:10px;' +
        'font:13px/1.5 "Microsoft YaHei",system-ui,sans-serif;color:#f1f5f9;' +
        'box-shadow:0 8px 24px rgba(0,0,0,.45);border-left:3px solid ' + cfg.bar + ';' +
        'background:' + cfg.bg + ';opacity:0;transform:translateY(10px);' +
        'transition:opacity .22s ease,transform .22s ease;';
      var ic = document.createElement('span');
      ic.textContent = cfg.icon;
      ic.style.cssText = 'flex:0 0 auto;color:' + cfg.bar + ';font-weight:700;';
      var tx = document.createElement('span');
      tx.style.cssText = 'flex:1 1 auto;word-break:break-all;';
      el.appendChild(ic);
      el.appendChild(tx);
      el._text = tx;
      h.appendChild(el);
      requestAnimationFrame(function(){
        requestAnimationFrame(function(){
          el.style.opacity = '1';
          el.style.transform = 'translateY(0)';
        });
      });
    }
    el._text.textContent = String(text == null ? '' : text);
    if(kind === 'done' || kind === 'failed'){
      var p = h.querySelector('[data-kind="progress"]');
      if(p) remove(p);
    }
    trim();
    if(kind !== 'progress'){
      clearTimeout(el._timer);
      el._timer = setTimeout(function(){ remove(el); }, kind === 'failed' ? 6000 : 3500);
    }
    return true;
  };
  return true;
})()"#;

/// 向平台窗口右下角推送一条 toast（失败只记日志，不影响主流程）
pub async fn push(port: u16, platform: &str, kind: &str, text: &str) {
    let js = format!(
        "{}\n;(function(){{ try{{ window.__dtToast({}, {}); }}catch(e){{}} return true; }})()",
        HOST_JS,
        json!(kind),
        json!(text)
    );
    if let Err(e) = cookies::cdp_eval_js(port, platform, &js).await {
        eprintln!("[toast] 推送失败({}/{}): {}", platform, kind, e);
    }
}
