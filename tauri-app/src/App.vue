<script setup lang="ts">
import { ref, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

type Platform = "dy" | "xhs";
type Tab = "launch" | "login" | "settings" | "log";
/// 快捷键录制目标：两个平台窗口启动键 + 窗口内下载键
type ShortcutTarget = Platform | "download";

type CookieStatus = {
  window_open: boolean;
  login: boolean;
  count: number;
  login_cookies?: Array<{ name: string; expires: number | null; expired: boolean; is_session: boolean }>;
  note?: string;
  error?: string;
};
type VerifyResult = { platform: string; logged_in: boolean; detail: any };
type SaveResult = { ok: boolean; count: number; login: boolean; file: string };
type Settings = {
  download_dir: string;
  shortcuts: { dy: string; xhs: string };
  download_shortcut: string;
  dy_quality: string;
};

const tab = ref<Tab>("launch");
const dyStatus = ref<CookieStatus | null>(null);
const xhsStatus = ref<CookieStatus | null>(null);
const dyVerify = ref<VerifyResult | null>(null);
const xhsVerify = ref<VerifyResult | null>(null);
const settings = ref<Settings | null>(null);
const dyShortcutInput = ref("");
const xhsShortcutInput = ref("");
const downloadShortcutInput = ref("");
const dyQuality = ref("");
const message = ref("");

// ---- P1-2 sidecar 引擎日志透传 ----
const sidecarLogs = ref<string[]>([]);
const sidecarRunning = ref<string | null>(null);

function appendLog(line: string) {
  sidecarLogs.value.push(line);
  // 仅保留最近 300 行，避免长跑时内存膨胀
  if (sidecarLogs.value.length > 300) {
    sidecarLogs.value.splice(0, sidecarLogs.value.length - 300);
  }
}

async function openWindow(p: Platform) {
  message.value = `启动 ${p} 窗口...`;
  try {
    const r = await invoke<string>("open_platform_window", { platform: p });
    message.value = `✅ ${r}`;
    setTimeout(() => refreshStatus(p), 4000);
  } catch (e) {
    message.value = `❌ 启动失败: ${String(e)}`;
  }
}

async function refreshStatus(p: Platform) {
  try {
    const r = await invoke<CookieStatus>("cookie_status", { platform: p });
    if (p === "dy") dyStatus.value = r;
    else xhsStatus.value = r;
  } catch (e) {
    console.error(`[panel] cookie_status ${p} 失败:`, e);
  }
}

async function verifyLogin(p: Platform) {
  message.value = `验证 ${p} 真实登录态...`;
  try {
    const r = await invoke<VerifyResult>("verify_login", { platform: p });
    if (p === "dy") dyVerify.value = r;
    else xhsVerify.value = r;
    message.value = `🔍 ${p} verify_login: ${r.logged_in ? "已登录" : "未登录"}\n${JSON.stringify(r.detail)}`;
  } catch (e) {
    message.value = `❌ 验证失败: ${String(e)}`;
  }
}

function statusText(s: CookieStatus | null): string {
  if (!s) return "未检测";
  if (!s.window_open) return "窗口未启动";
  return s.login ? "已登录(cookie 存在)" : "未登录";
}

function statusClass(s: CookieStatus | null): string {
  if (!s || !s.window_open) return "warn";
  return s.login ? "ok" : "warn";
}

async function save(p: Platform) {
  message.value = `保存 ${p} cookies...`;
  try {
    const r = await invoke<SaveResult>("save_cookies", { platform: p });
    if (r.ok) {
      message.value = `✅ ${p} 已保存 ${r.count} 条 cookies（登录: ${r.login ? "是" : "否"}）\n文件: ${r.file}`;
      await refreshStatus(p);
    } else {
      message.value = `❌ 保存失败: ${JSON.stringify(r)}`;
    }
  } catch (e) {
    message.value = `❌ 保存失败: ${String(e)}`;
  }
}

async function loadSettings() {
  try {
    const s = await invoke<Settings>("get_settings");
    settings.value = s;
    dyShortcutInput.value = s.shortcuts.dy;
    xhsShortcutInput.value = s.shortcuts.xhs;
    downloadShortcutInput.value = s.download_shortcut;
    dyQuality.value = s.dy_quality;
  } catch (e) {
    console.error("[panel] 加载设置失败:", e);
  }
}

async function chooseDownloadDir() {
  try {
    const dir = await openDialog({ directory: true, multiple: false, title: "选择下载目录" });
    if (typeof dir === "string" && dir.length > 0) {
      message.value = `设置下载目录: ${dir}`;
      const s = await invoke<Settings>("set_download_dir", { dir });
      settings.value = s;
      message.value = `✅ 下载目录已保存: ${s.download_dir}`;
    }
  } catch (e) {
    message.value = `❌ 选择目录失败: ${String(e)}`;
  }
}

async function saveShortcut(p: Platform) {
  const v = p === "dy" ? dyShortcutInput.value.trim() : xhsShortcutInput.value.trim();
  message.value = `保存 ${p} 快捷键: ${v || "(已禁用)"}...`;
  try {
    const s = await invoke<Settings>("set_shortcut", { platform: p, shortcut: v });
    settings.value = s;
    if (p === "dy") dyShortcutInput.value = s.shortcuts.dy;
    else xhsShortcutInput.value = s.shortcuts.xhs;
    message.value = `✅ ${p} 快捷键已保存: ${s.shortcuts[p] || "(已禁用)"}`;
  } catch (e) {
    message.value = `❌ 快捷键保存失败: ${String(e)}`;
  }
}

// 快捷键录制：input 聚焦后按下任意组合键，自动识别填入
function captureShortcut(e: KeyboardEvent, target: ShortcutTarget) {
  // Tab 不拦截，方便切换焦点
  if (e.key === "Tab") return;
  e.preventDefault();
  // 仅按 modifier 不算（等用户继续按主键）
  if (["Alt", "Control", "Shift", "Meta"].includes(e.key)) return;
  // 必须至少一个 modifier，否则单键全局快捷键会干扰正常输入
  if (!e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
    message.value = "⚠️ 快捷键必须包含至少一个修饰键（Alt/Ctrl/Shift/Super）";
    return;
  }
  const parts: string[] = [];
  if (e.altKey) parts.push("Alt");
  if (e.ctrlKey) parts.push("Control");
  if (e.shiftKey) parts.push("Shift");
  if (e.metaKey) parts.push("Super");
  let key = e.key;
  // 单字符（字母/数字）转大写；特殊键（F1/Enter 等）保持原样
  if (key.length === 1) key = key.toUpperCase();
  parts.push(key);
  const combo = parts.join("+");
  if (target === "dy") dyShortcutInput.value = combo;
  else if (target === "xhs") xhsShortcutInput.value = combo;
  else downloadShortcutInput.value = combo;
  message.value = `已捕获 ${target} 快捷键: ${combo}（点击"保存"生效）`;
}

// 录制模式：input 聚焦/失焦时切换 placeholder
const recording = ref<ShortcutTarget | null>(null);
function onShortcutFocus(p: ShortcutTarget) {
  recording.value = p;
}
function onShortcutBlur() {
  recording.value = null;
}

// 抖音清晰度档位（"" = 最高）
async function setDyQuality() {
  try {
    const s = await invoke<Settings>("set_dy_quality", { quality: dyQuality.value });
    settings.value = s;
    dyQuality.value = s.dy_quality;
    message.value = `✅ 抖音清晰度已保存: ${s.dy_quality ? s.dy_quality + "P" : "最高"}`;
  } catch (e) {
    message.value = `❌ 清晰度保存失败: ${String(e)}`;
  }
}

// 下载快捷键（仅平台窗口聚焦时生效）
async function saveDownloadShortcut() {
  const v = downloadShortcutInput.value.trim();
  try {
    const s = await invoke<Settings>("set_download_shortcut", { shortcut: v });
    settings.value = s;
    downloadShortcutInput.value = s.download_shortcut;
    message.value = `✅ 下载快捷键已保存: ${s.download_shortcut || "(已禁用)"}`;
  } catch (e) {
    message.value = `❌ 下载快捷键保存失败: ${String(e)}`;
  }
}

function selectTab(t: Tab) {
  tab.value = t;
}

// ---- sidecar 引擎管道自检 ----
async function runEngineSelfTest() {
  sidecarLogs.value = [];
  sidecarRunning.value = null;

  // 用 --help 做纯管道自检：只验证 spawn / 流式日志 / 退出码，不触碰浏览器、不发起下载
  appendLog("[panel] spawn dt-engine（task=dy-download）--help（仅验证管道，不触碰浏览器）");
  try {
    const runId = await invoke<string>("sidecar_spawn", {
      task: "dy-download",
      args: ["--help"],
    });
    sidecarRunning.value = runId;
    appendLog(`[panel] run_id = ${runId}`);
  } catch (e) {
    appendLog(`[panel] ❌ spawn 失败: ${String(e)}`);
  }
}

async function stopEngine() {
  if (!sidecarRunning.value) return;
  try {
    const ok = await invoke<boolean>("sidecar_kill", { runId: sidecarRunning.value });
    appendLog(`[panel] sidecar_kill → ${ok}`);
  } catch (e) {
    appendLog(`[panel] ❌ kill 失败: ${String(e)}`);
  }
  sidecarRunning.value = null;
}

onMounted(async () => {
  loadSettings();

  // sidecar 日志 / 结束事件透传
  await listen<{ run_id: string; engine: string; stream: string; line: string }>(
    "sidecar-log",
    (ev) => {
      const p = ev.payload;
      const prefix = p.stream === "stdout" ? "" : `[${p.stream}] `;
      appendLog(`${prefix}${p.line}`);
    }
  );
  await listen<{ run_id: string; engine: string; code: number | null }>(
    "sidecar-exit",
    (ev) => {
      const p = ev.payload;
      appendLog(`[panel] ${p.engine} 已退出 code=${p.code ?? "null"}`);
      if (sidecarRunning.value === p.run_id) sidecarRunning.value = null;
    }
  );

  setTimeout(() => {
    refreshStatus("dy");
    refreshStatus("xhs");
  }, 1500);
});
</script>

<template>
  <main class="panel">
    <h1>DownloadTooler</h1>
    <p class="version">配置中心 · Tauri 2.0 P1-1</p>

    <div class="tabs">
      <button :class="{ active: tab === 'launch' }" @click="selectTab('launch')">启动</button>
      <button :class="{ active: tab === 'login' }" @click="selectTab('login')">登录态</button>
      <button :class="{ active: tab === 'settings' }" @click="selectTab('settings')">设置</button>
      <button :class="{ active: tab === 'log' }" @click="selectTab('log')">日志</button>
    </div>

    <!-- 启动标签 -->
    <section v-if="tab === 'launch'" class="card launch-card">
      <h2>启动平台窗口</h2>
      <p class="hint">点击按钮启动 / 聚焦对应平台窗口；也可使用快捷键触发。</p>
      <div class="launch-row">
        <button class="launch-btn dy" @click="openWindow('dy')">
          <span class="brand">抖音</span>
          <span class="shortcut">快捷键: {{ settings?.shortcuts.dy || "未绑定" }}</span>
        </button>
        <button class="launch-btn xhs" @click="openWindow('xhs')">
          <span class="brand">小红书</span>
          <span class="shortcut">快捷键: {{ settings?.shortcuts.xhs || "未绑定" }}</span>
        </button>
      </div>
      <p class="muted">CDP 端口：抖音 9357 · 小红书 9361</p>
    </section>

    <!-- 登录态标签 -->
    <section v-else-if="tab === 'login'" class="card">
      <h2>登录态检测</h2>

      <div class="platform-block">
        <h3>抖音</h3>
        <p>Cookie 状态: <span :class="statusClass(dyStatus)">{{ statusText(dyStatus) }}</span> ({{ dyStatus?.count ?? 0 }} cookies)</p>
        <p v-if="dyVerify" class="verify-line">真实登录态: <span :class="dyVerify.logged_in ? 'ok' : 'warn'">{{ dyVerify.logged_in ? "已登录" : "未登录" }}</span> <span class="muted">({{ dyVerify.detail?.method }})</span></p>
        <div class="btn-row">
          <button @click="openWindow('dy')">启动/聚焦</button>
          <button @click="verifyLogin('dy')">验证登录态</button>
          <button @click="save('dy')">保存 Cookie</button>
          <button @click="refreshStatus('dy')">刷新</button>
        </div>
      </div>

      <div class="divider"></div>

      <div class="platform-block">
        <h3>小红书</h3>
        <p>Cookie 状态: <span :class="statusClass(xhsStatus)">{{ statusText(xhsStatus) }}</span> ({{ xhsStatus?.count ?? 0 }} cookies)</p>
        <p v-if="xhsVerify" class="verify-line">真实登录态: <span :class="xhsVerify.logged_in ? 'ok' : 'warn'">{{ xhsVerify.logged_in ? "已登录" : "未登录" }}</span> <span class="muted">({{ xhsVerify.detail?.method }})</span></p>
        <div class="btn-row">
          <button @click="openWindow('xhs')">启动/聚焦</button>
          <button @click="verifyLogin('xhs')">验证登录态</button>
          <button @click="save('xhs')">保存 Cookie</button>
          <button @click="refreshStatus('xhs')">刷新</button>
        </div>
      </div>
    </section>

    <!-- 设置标签 -->
    <section v-else-if="tab === 'settings'" class="card">
      <h2>设置</h2>

      <div class="setting-block">
        <h3>下载目录</h3>
        <p class="muted">抖音和小红书下载的同一使用该目录。</p>
        <div class="dir-row">
          <code class="dir-path">{{ settings?.download_dir || "(未设置，使用系统 Downloads)" }}</code>
          <button @click="chooseDownloadDir">选择目录</button>
        </div>

        <h3>抖音清晰度</h3>
        <p class="muted">仅影响视频；图集始终下载原图。默认最高。</p>
        <div class="dir-row">
          <select v-model="dyQuality" class="sel" @change="setDyQuality">
            <option value="">最高</option>
            <option value="1080">1080P</option>
            <option value="720">720P</option>
            <option value="540">540P</option>
          </select>
        </div>
      </div>

      <div class="divider"></div>

      <div class="setting-block">
        <h3>窗口快捷键</h3>
        <p class="muted">格式如 Alt+1、Ctrl+Shift+D、CommandOrControl+2；留空则禁用该平台快捷键。</p>

        <div class="shortcut-row">
          <label>抖音</label>
          <input
            v-model="dyShortcutInput"
            :placeholder="recording === 'dy' ? '按下快捷键组合...' : '点击此处后按下组合键'"
            @keydown="captureShortcut($event, 'dy')"
            @focus="onShortcutFocus('dy')"
            @blur="onShortcutBlur"
          />
          <button @click="saveShortcut('dy')">保存</button>
          <button class="ghost" @click="dyShortcutInput = 'Alt+1'">恢复默认</button>
        </div>

        <div class="shortcut-row">
          <label>小红书</label>
          <input
            v-model="xhsShortcutInput"
            :placeholder="recording === 'xhs' ? '按下快捷键组合...' : '点击此处后按下组合键'"
            @keydown="captureShortcut($event, 'xhs')"
            @focus="onShortcutFocus('xhs')"
            @blur="onShortcutBlur"
          />
          <button @click="saveShortcut('xhs')">保存</button>
          <button class="ghost" @click="xhsShortcutInput = 'Alt+2'">恢复默认</button>
        </div>

        <h3>下载快捷键（窗口内生效）</h3>
        <p class="muted">仅在抖音 / 小红书窗口聚焦时生效；留空则禁用。默认 Ctrl+D。</p>
        <div class="shortcut-row">
          <label>下载</label>
          <input
            v-model="downloadShortcutInput"
            :placeholder="recording === 'download' ? '按下快捷键组合...' : '点击此处后按下组合键'"
            @keydown="captureShortcut($event, 'download')"
            @focus="onShortcutFocus('download')"
            @blur="onShortcutBlur"
          />
          <button @click="saveDownloadShortcut">保存</button>
          <button class="ghost" @click="downloadShortcutInput = 'Ctrl+D'">恢复默认</button>
        </div>
      </div>

      <div class="divider"></div>

      <p class="muted small">关闭窗口将最小化到系统托盘；通过托盘右键菜单"显示主窗口"恢复，或"退出"完全关闭。</p>
    </section>

    <!-- 日志标签 -->
    <section v-else class="card log-card">
      <h2>引擎日志</h2>
      <p class="muted">Python 引擎（sidecar）的实时输出；下载日志也会打印到这里。「运行引擎自检」仅验证管道（spawn / 流式日志 / 退出码），不触碰浏览器。</p>
      <div class="btn-row">
        <button @click="runEngineSelfTest">运行引擎自检</button>
        <button class="ghost" :disabled="!sidecarRunning" @click="stopEngine">结束引擎</button>
        <button class="ghost" @click="sidecarLogs = []">清空日志</button>
      </div>
      <pre class="sidecar-log tall">{{ sidecarLogs.length ? sidecarLogs.join("\n") : "（暂无日志）" }}</pre>
    </section>

    <pre v-if="message" class="message">{{ message }}</pre>

    <footer>
      <p class="hint">提示：启动应用后仅 panel_main 自动打开；点击"启动"标签拉起 dy_main / xhs_main；每 30 秒自动导出 cookies.json。</p>
    </footer>
  </main>
</template>

<style scoped>
.panel {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  padding: 16px;
  color: #333;
}
h1 { margin: 0 0 4px 0; font-size: 20px; }
.version { margin: 0 0 16px 0; color: #888; font-size: 12px; }
.tabs { display: flex; gap: 4px; margin-bottom: 12px; }
.tabs button {
  flex: 1; padding: 7px 6px; border: 1px solid #ddd; border-radius: 4px;
  background: #f5f5f5; cursor: pointer; font-size: 13px;
}
.tabs button.active { background: #4a90e2; color: #fff; border-color: #4a90e2; }
.card {
  border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin-bottom: 12px;
}
.card h2 { margin: 0 0 8px 0; font-size: 16px; }
.card h3 { margin: 12px 0 4px 0; font-size: 14px; color: #4a90e2; }
.card p { margin: 4px 0; font-size: 13px; }
.btn-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.card button {
  padding: 6px 10px; font-size: 13px;
  border: 1px solid #4a90e2; background: #fff; color: #4a90e2;
  border-radius: 4px; cursor: pointer;
}
.card button:hover { opacity: 0.85; }
.card button.ghost { border-color: #aaa; color: #888; }

.launch-card .hint { color: #888; font-size: 12px; margin-bottom: 12px; }
.launch-row {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;
}
.launch-btn {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 18px 8px; min-height: 80px;
  border: none; border-radius: 8px; cursor: pointer;
  font-size: 14px; color: #fff;
  transition: transform 0.1s, opacity 0.2s;
}
.launch-btn:hover { opacity: 0.9; }
.launch-btn:active { transform: scale(0.98); }
.launch-btn.dy { background: linear-gradient(135deg, #000000, #1a1a1a); }
.launch-btn.xhs { background: linear-gradient(135deg, #ff2442, #ff6080); }
.launch-btn .brand { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.launch-btn .shortcut { font-size: 11px; opacity: 0.85; }

.platform-block { padding: 6px 0; }
.divider { height: 1px; background: #eee; margin: 12px 0; }

.verify-line { margin-top: 4px; padding: 4px 6px; background: #f0f4ff; border-radius: 4px; font-size: 12px; }

.setting-block { padding: 6px 0; }
.dir-row { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.dir-path {
  flex: 1; padding: 6px 8px; background: #f5f5f5; border-radius: 4px;
  font-size: 12px; color: #555; word-break: break-all;
}
.shortcut-row {
  display: grid; grid-template-columns: 60px 1fr auto auto; gap: 6px;
  align-items: center; margin-top: 8px;
}
.shortcut-row input {
  padding: 6px 8px; font-size: 13px;
  border: 1px solid #ddd; border-radius: 4px;
}
.shortcut-row input:focus { outline: none; border-color: #4a90e2; }
.shortcut-row label { font-size: 13px; color: #555; }
select.sel {
  padding: 6px 8px; font-size: 13px; min-width: 120px;
  border: 1px solid #ddd; border-radius: 4px; background: #fff;
}
select.sel:focus { outline: none; border-color: #4a90e2; }

.ok { color: #28a745; font-weight: 600; }
.warn { color: #ffc107; font-weight: 600; }
.muted { color: #888; font-size: 12px; }
.muted.small { font-size: 11px; }
.message {
  margin-top: 12px; padding: 8px; background: #f8f9fa; border-radius: 4px;
  font-size: 12px; white-space: pre-wrap; word-break: break-all;
}
.sidecar-log {
  margin-top: 8px; padding: 8px; max-height: 220px; overflow: auto;
  background: #1e1e1e; color: #d4d4d4; border-radius: 4px;
  font-size: 11px; line-height: 1.45; white-space: pre-wrap; word-break: break-all;
}
.sidecar-log.tall { max-height: 420px; min-height: 200px; }
.card button:disabled { opacity: 0.45; cursor: not-allowed; }
.hint { margin: 12px 0 0 0; font-size: 11px; color: #999; }
</style>
