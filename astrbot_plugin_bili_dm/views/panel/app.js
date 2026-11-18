/* B站私信面板前端
   同一个文件跑两种宿主：
   - AstrBot 插件视图（iframe + window.AstrBotPluginView bridge）
   - 独立运行（同源 fetch + SSE）
*/

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage || null;
const MODE = bridge ? "astrbot" : "standalone";

const S = {
  state: null,
  sessions: [],
  active: 0,
  messages: [],
  ids: [],
  lastDay: "",
  sseId: null,
  timer: null,
  busy: false,
  palette: null,
  atBottom: true,
  emotes: null,
  emoteMap: null,
};

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
  online: "在线",
  waiting_login: "未就绪",
  error: "无适配器",
  demo: "演示",
  idle: "未启动",
};

/* ---------------- 基础 ---------------- */
const pad = (n) => String(n).padStart(2, "0");

// 兼容历史数据里混入的微秒/毫秒时间戳，坏值直接当没有
function saneTs(value) {
  let n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 0;
  if (n >= 1e14) n = Math.floor(n / 1e6);
  else if (n >= 1e11) n = Math.floor(n / 1e3);
  return n;
}

function fmtTime(ts) {
  const t = saneTs(ts);
  if (!t) return "";
  const d = new Date(t * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtSessionTime(ts) {
  const t = saneTs(ts);
  if (!t) return "";
  const d = new Date(t * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtTime(t);
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}-${pad(d.getDate())}`;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtDay(ts) {
  const t = saneTs(ts);
  if (!t) return "";
  const d = new Date(t * 1000);
  const now = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, now)) return "今天";
  if (same(d, new Date(now.getTime() - 86400000))) return "昨天";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtAgo(ts) {
  const t = saneTs(ts);
  if (!t) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* iframe 里可能不可用 */ }
  try {
    const input = document.createElement("textarea");
    input.value = text;
    input.style.cssText = "position:fixed;opacity:0;left:-9999px";
    document.body.appendChild(input);
    input.select();
    const ok = document.execCommand("copy");
    input.remove();
    return ok;
  } catch (_) {
    return false;
  }
}

function showToast(text) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 2800);
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

function paintAvatar(el, name, face) {
  if (face) {
    el.style.backgroundImage = `url("${face}")`;
    el.textContent = "";
  } else {
    el.style.backgroundImage = "";
    el.textContent = (name || "?").trim().slice(0, 1).toUpperCase();
  }
}

/* ---------------- 传输 ---------------- */
async function httpJson(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail || data.message)) || `请求失败（${res.status}）`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

const Api = {
  async get(name, params = {}) {
    if (bridge) return bridge.apiGet(name, params);
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ).toString();
    return httpJson(`/api/${name}${qs ? `?${qs}` : ""}`);
  },
  async post(name, body = {}) {
    if (bridge) return bridge.apiPost(name, body);
    return httpJson(`/api/${name}`, { method: "POST", body: JSON.stringify(body) });
  },
};

function subscribeEvents(onEvent) {
  if (bridge) {
    bridge
      .subscribeSSE("events", {
        onMessage(event) {
          try {
            onEvent(typeof event.parsed === "object" ? event.parsed : JSON.parse(event.raw));
          } catch (_) { /* 心跳 */ }
        },
        onError() { log("sys", "实时通道异常"); },
      })
      .then((id) => { S.sseId = id; })
      .catch((err) => log("err", `实时订阅失败：${err.message}`));
    return;
  }
  const es = new EventSource("/api/events");
  es.onopen = () => log("sys", "实时通道已连接");
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)); } catch (_) { /* ignore */ }
  };
  es.onerror = () => log("sys", "实时通道断开，浏览器会自动重连");
}

/* ---------------- 状态 ---------------- */
function renderState(payload) {
  if (!payload) return;
  S.state = payload;
  const cfg = payload.config || {};
  const st = payload.status || {};
  const ad = payload.adapter || {};
  const run = payload.runtime || {};

  const myName = cfg.my_name || (ad.found ? "B站账号" : "未就绪");
  $("myName").textContent = myName;
  $("myUid").textContent = ad.uid ? `UID ${ad.uid}` : "—";
  paintAvatar($("myAvatar"), myName, cfg.my_face);

  const pill = $("statusPill");
  pill.textContent = STATUS_TEXT[st.state] || st.state || "未知";
  pill.className = "pill " + ({
    online: "online", waiting_login: "waiting", error: "error", demo: "demo",
  }[st.state] || "");

  $("adapterName").textContent = ad.display || ad.name || "未找到";
  $("adapterUid").textContent = ad.uid ? String(ad.uid) : "—";
  $("scopeText").textContent = cfg.scope === "single"
    ? `只看 ${cfg.peer_uid || "未设置"}`
    : "所有会话";
  const dir = run.data_dir || "";
  $("runData").textContent = dir ? dir.replace(/^.*[\\/]/, "…/") : "—";
  $("runData").title = dir;
  $("runCount").textContent =
    `${(payload.messages && payload.messages.count) || 0} 条 / ${(payload.sessions && payload.sessions.count) || 0} 会话`;

  const hint = $("adapterHint");
  if (!ad.found) {
    hint.textContent = "没找到 bilibili 适配器：去「配置 → 消息平台」启用它";
    hint.style.color = "var(--red)";
  } else if (!ad.ready) {
    hint.textContent = "适配器已加载，客户端还在初始化";
    hint.style.color = "var(--amber)";
  } else {
    hint.textContent = `已接入 ${ad.display || ad.name}${ad.poll_interval ? ` · 适配器轮询 ${ad.poll_interval}s` : ""}`;
    hint.style.color = "";
  }

  const banner = $("banner");
  if (!ad.found) {
    banner.hidden = false;
    banner.textContent = "没有可用的 B 站私信适配器。先在 AstrBot 的「配置 → 消息平台」里启用 bilibili，再回到这里刷新。";
  } else if (!ad.ready) {
    banner.hidden = false;
    banner.textContent = "适配器已加载但客户端还没就绪，等几秒点「刷新会话」。";
  } else {
    banner.hidden = true;
  }

  if (!S.timer) startTimer();
}

/* ---------------- 会话列表 ---------------- */
function renderSessions() {
  const box = $("sessionList");
  const keyword = ($("search").value || "").trim().toLowerCase();
  const selfUid = Number((S.state && S.state.config && S.state.config.my_uid) || 0);
  const items = S.sessions.filter((s) => {
    if (selfUid && Number(s.peer_uid) === selfUid) return false;  // 不把自己列进会话
    if (!keyword) return true;
    return String(s.name || "").toLowerCase().includes(keyword) || String(s.peer_uid).includes(keyword);
  });

  box.innerHTML = "";
  if (!items.length) {
    const hint = document.createElement("div");
    hint.className = "list-hint";
    hint.textContent = S.sessions.length
      ? "没有匹配的会话"
      : (S.state && S.state.config.scope === "single"
        ? "当前是「只看指定的人」，去设置里填一个 UID 或切换范围"
        : "还没有会话，收到私信后会出现在这里");
    box.appendChild(hint);
    return;
  }

  for (const s of items) {
    const row = document.createElement("button");
    row.className = "session" + (s.peer_uid === S.active ? " active" : "");
    row.dataset.peer = s.peer_uid;
    const unread = Number(s.unread || 0);

    row.innerHTML = `
      <div class="avatar sm" data-avatar></div>
      <div class="session-main">
        <div class="session-top">
          <span class="session-name"></span>
          <span class="session-time">${fmtSessionTime(s.last_ts)}</span>
        </div>
        <div class="session-bottom">
          <span class="session-preview"></span>
          ${unread > 0 ? `<span class="badge">${unread > 99 ? "99+" : unread}</span>` : ""}
        </div>
      </div>`;

    row.querySelector(".session-name").textContent = s.name || `UID ${s.peer_uid}`;
    row.querySelector(".session-preview").textContent = s.last_text || "（还没有消息）";
    paintAvatar(row.querySelector("[data-avatar]"), s.name, s.face);
    row.addEventListener("click", () => openSession(s.peer_uid));
    box.appendChild(row);
  }
}

async function loadSessions() {
  try {
    const data = await Api.get("sessions");
    S.sessions = data.sessions || [];
    renderSessions();
    return S.sessions;
  } catch (err) {
    log("err", `会话列表：${err.message}`);
    return [];
  }
}

/* ---------------- 消息 ---------------- */
function emptyNode(text, sub) {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.innerHTML = `<div class="empty-icon">✉</div>
    <div class="empty-title">${esc(text)}</div>
    <div class="empty-sub">${esc(sub || "")}</div>`;
  return empty;
}

function daySep(ts) {
  const sep = document.createElement("div");
  sep.className = "day-sep";
  sep.innerHTML = `<span>${fmtDay(ts)}</span>`;
  return sep;
}

/* B 站分享消息：raw.content 里带封面、标题、作者和视频链接。
   适配器转成 AstrBot 事件时只保留文字，所以这里从原始数据还原成官方那种卡片。 */
function videoCardFromRaw(raw) {
  if (!raw || typeof raw !== "object") return null;
  let data = raw.content;
  if (typeof data === "string") {
    try { data = JSON.parse(data); } catch (_) { return null; }
  }
  if (!data || typeof data !== "object") return null;
  const bvid = String(data.bvid || "");
  let url = String(data.url || "");
  if (!url && bvid) url = `https://www.bilibili.com/video/${bvid}`;
  const cover = String(data.thumb || data.image || "").replace(/^http:\/\//i, "https://");
  if (!url || !cover) return null;
  return {
    title: String(data.title || data.headline || "分享的视频"),
    author: String(data.author || ""),
    cover,
    url,
  };
}

function bubbleNode(msg) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${msg.direction === "out" ? "out" : "in"}`;
  wrap.dataset.id = msg.id;

  const cfg = S.state ? S.state.config : {};
  const name = msg.direction === "out" ? (cfg.my_name || "我") : (currentSession()?.name || "对方");
  const av = document.createElement("div");
  av.className = "avatar";
  paintAvatar(av, name, msg.direction === "out" ? cfg.my_face : (currentSession()?.face || ""));

  const body = document.createElement("div");
  body.className = "msg-body";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (msg.kind === "image" && /^https?:/.test(msg.text)) {
    const img = document.createElement("img");
    img.src = msg.text;
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    // 图片晚于消息渲染完成，高度变了要重新贴底（仅在用户本来就在底部时）
    img.addEventListener("load", () => { if (S.atBottom) scrollThreadToBottom("smooth"); });
    bubble.appendChild(img);
  } else {
    const card = videoCardFromRaw(msg.raw);
    if (!card) {
      renderTextWithEmotes(bubble, msg.text);
    } else {
      bubble.classList.add("card-bubble");
      const link = document.createElement("a");
      link.className = "video-card";
      link.href = card.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = `打开视频：${card.title}`;

      const cover = document.createElement("div");
      cover.className = "vc-cover";
      const img = document.createElement("img");
      img.src = card.cover;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("error", () => {
        img.style.display = "none";
        cover.classList.add("vc-noimg");
      });
      img.addEventListener("load", () => { if (S.atBottom) scrollThreadToBottom("smooth"); });
      const play = document.createElement("span");
      play.className = "vc-play";
      play.textContent = "▶";
      cover.append(img, play);

      const body = document.createElement("div");
      body.className = "vc-body";
      const title = document.createElement("div");
      title.className = "vc-title";
      title.textContent = card.title;
      const author = document.createElement("div");
      author.className = "vc-author";
      const up = document.createElement("span");
      up.className = "vc-up";
      up.textContent = "UP";
      author.append(up, document.createTextNode(card.author || "B站视频"));
      body.append(title, author);
      link.append(cover, body);

      // 注意：window.open 带 "noopener" 时即使成功也会返回 null，
      // 会被误判成「弹窗被拦」而再触发一次面板内跳转。这里不带该参数，
      // 拿到窗口后手动断开 opener，失败时只复制链接、不在面板内打开。
      link.addEventListener("click", (e) => {
        e.preventDefault();
        let opened = null;
        try {
          opened = window.open(card.url, "_blank");
          if (opened) {
            try { opened.opener = null; } catch (_) { /* 跨域窗口 */ }
            return;
          }
        } catch (_) { /* iframe sandbox 禁止弹窗 */ }
        copyText(card.url).then((ok) => {
          showToast(ok ? "链接已复制，粘贴到浏览器打开" : `打开失败，链接：${card.url}`);
        });
      });
      bubble.appendChild(link);
    }
  }

  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.innerHTML = `<span>${fmtTime(msg.ts)}</span>`;

  body.append(bubble, meta);
  wrap.append(av, body);
  return wrap;
}

function currentSession() {
  return S.sessions.find((s) => s.peer_uid === S.active) || null;
}

/* 打开面板/切换会话要立刻停在最新一条：
   .thread 设了 scroll-behavior:smooth，直接改 scrollTop 会变成慢动画；
   图片又是异步加载的，加载完后高度会变。所以强制 auto 并等下一帧再对齐一次。 */
function scrollThreadToBottom(mode = "instant") {
  const thread = $("thread");
  if (!thread) return;
  if (mode === "smooth") {
    // 新消息进来时像官方聊天一样滑下去，不硬跳
    thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
  } else {
    const previous = thread.style.scrollBehavior;
    thread.style.scrollBehavior = "auto";
    thread.scrollTop = thread.scrollHeight;
    requestAnimationFrame(() => {
      thread.scrollTop = thread.scrollHeight;
      thread.style.scrollBehavior = previous;
    });
  }
  S.atBottom = true;
}

function renderMessages(toBottom = false) {
  const thread = $("thread");
  thread.innerHTML = "";
  S.ids = [];
  S.lastDay = "";

  if (!S.active) {
    thread.appendChild(emptyNode("选择一个会话", "左侧点开一个对话，或从设置里指定一个人"));
    return;
  }
  if (!S.messages.length) {
    thread.appendChild(emptyNode("这里还没有消息记录", "收到新消息或点「刷新」后会显示"));
    return;
  }

  for (const msg of S.messages) {
    const day = fmtDay(msg.ts);
    if (day !== S.lastDay) {
      S.lastDay = day;
      thread.appendChild(daySep(msg.ts));
    }
    thread.appendChild(bubbleNode(msg));
    S.ids.push(msg.id);
  }
  if (toBottom) scrollThreadToBottom();
}

function syncMessages(list) {
  const thread = $("thread");
  const newIds = list.map((m) => m.id);
  const prefixMatch =
    S.ids.length > 0 &&
    S.ids.length <= newIds.length &&
    S.ids.every((id, i) => id === newIds[i]);

  const atBottom =
    !S.ids.length ||
    thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120 ||
    S.atBottom;

  S.messages = list;
  if (!prefixMatch) {
    renderMessages(atBottom);
    return;
  }
  const known = new Set(S.ids);
  let added = 0;
  for (const msg of list) {
    if (known.has(msg.id)) continue;
    const day = fmtDay(msg.ts);
    if (day !== S.lastDay) {
      S.lastDay = day;
      thread.appendChild(daySep(msg.ts));
    }
    thread.appendChild(bubbleNode(msg));
    S.ids.push(msg.id);
    added += 1;
  }
  // 只有真正新增了消息才动滚动条，定时刷新不应触发动画
  if (added && atBottom) scrollThreadToBottom("smooth");
}

async function openSession(peer) {
  if (!peer) return;
  S.active = Number(peer);
  S.messages = [];
  S.ids = [];
  renderSessions();

  const sess = currentSession();
  $("headName").textContent = sess?.name || `UID ${peer}`;
  $("headSub").textContent = `UID ${peer}`;
  paintAvatar($("headAvatar"), sess?.name || "", sess?.face || "");
  $("peerPill").hidden = true;

  renderMessages(true);
  await loadMessages();
  await markRead();
  scrollThreadToBottom();
  $("input").focus();
}

async function loadMessages(silent = false) {
  if (!S.active) return;
  try {
    const data = await Api.get("messages", { peer: S.active, limit: 60 });
    syncMessages(data.messages || []);
    const sess = currentSession();
    if (sess && data.peer && data.peer.name && !sess.name) {
      sess.name = data.peer.name;
      sess.face = data.peer.face || "";
      renderSessions();
    }
    if (!silent) log("sys", `已加载 ${S.messages.length} 条消息`);
  } catch (err) {
    if (!silent) $("sendError").textContent = err.message;
    log("err", err.message);
  }
}

async function markRead() {
  if (!S.active) return;
  try {
    await Api.post("read", { peer: S.active });
    const sess = currentSession();
    if (sess) { sess.unread = 0; renderSessions(); }
  } catch (_) { /* 忽略 */ }
}

/* ---------------- 发送 ---------------- */
async function send() {
  const input = $("input");
  const text = input.value.trim();
  if (!text || !S.active || S.busy) return;

  S.busy = true;
  $("btnSend").disabled = true;
  $("sendError").textContent = "";
  try {
    const res = await Api.post("send", { peer: S.active, text });
    if (res.message && res.message.id) {
      syncMessages([...S.messages, res.message]);
    }
    input.value = "";
    autoGrow();
    $("count").textContent = "0";
    const sess = currentSession();
    if (sess) { sess.last_text = text; sess.last_ts = Math.floor(Date.now() / 1000); renderSessions(); }
  } catch (err) {
    $("sendError").textContent = `发送失败：${err.message}`;
    log("err", err.message);
  } finally {
    S.busy = false;
    $("btnSend").disabled = false;
    input.focus();
  }
}

function autoGrow() {
  const el = $("input");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

/* ---------------- 日志 ---------------- */
function log(kind, text) {
  const box = $("log");
  if (!box) return;
  const item = document.createElement("div");
  item.className = `log-item ${kind}`;
  const t = new Date();
  item.innerHTML = `<span class="t">${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}</span>
    <span class="x">${esc(text)}</span>`;
  box.appendChild(item);
  while (box.children.length > 60) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

/* ---------------- 实时 ---------------- */
function handleEvent(payload) {
  if (!payload || !payload.type) return;
  if (payload.type === "message") {
    const msg = payload.data;
    if (Number(msg.peer_uid) === S.active) {
      syncMessages([...S.messages, msg]);
      markRead();
    }
    log(msg.direction === "out" ? "out" : "in", `${msg.direction === "out" ? "发出" : "收到"}：${String(msg.text).slice(0, 30)}`);
    loadSessions();
  } else if (payload.type === "sessions") {
    loadSessions();
  } else if (payload.type === "messages") {
    if (payload.data && Number(payload.data.peer) === S.active) loadMessages(true);
  } else if (payload.type === "status") {
    if (S.state) {
      S.state.status = payload.data;
      renderState(S.state);
    }
  }
}

/* ---------------- 定时刷新 ---------------- */
function startTimer() {
  if (S.timer) clearInterval(S.timer);
  const sec = Math.max(3, Number(S.state?.config?.panel_refresh || 10));
  S.timer = setInterval(async () => {
    if (document.hidden || !S.active) return;
    await loadMessages(true);
  }, sec * 1000);

  setInterval(() => {
    if (document.hidden) return;
    loadSessions();
    refreshPalette();  // 调色盘换壁纸/改设置后跟上
  }, 45000);
}

/* ---------------- 表情 ---------------- */
// 表情名 → 图片 URL。B站私信里表情是 [doge_金箍] 这种文本转义符，
// 官方客户端会渲染成图，这里用同步到的表情包数据做同样的替换。
function buildEmoteMap() {
  const map = new Map();
  const add = (key, url) => {
    const k = String(key || "").trim();
    if (k && url && !map.has(k)) map.set(k, url);
  };
  for (const pkg of (S.emotes && S.emotes.packages) || []) {
    for (const item of pkg.items || []) {
      const url = String(item.url || "").replace(/^http:/i, "https:");
      if (!url) continue;
      add(item.text, url);
      // alias 可能是 "笑,大笑" 这类逗号分隔的多个名字
      for (const a of String(item.alias || "").split(/[,，]/)) add(a, url);
    }
  }
  S.emoteMap = map;
}

// 把纯文本里的 [表情名] 换成内联图片，其余部分保持文本节点（不走 HTML 拼接，防注入）。
function renderTextWithEmotes(el, text) {
  const map = S.emoteMap;
  if (!map || !map.size) {
    el.textContent = text;
    return;
  }
  const re = /\[([^\[\]]{1,32})\]/g;
  let last = 0;
  let match;
  let replaced = false;
  while ((match = re.exec(text)) !== null) {
    const url = map.get(match[0]);
    if (!url) continue;
    if (match.index > last) {
      el.appendChild(document.createTextNode(text.slice(last, match.index)));
    }
    const img = document.createElement("img");
    img.className = "emote-inline";
    img.src = url;
    img.alt = match[0];
    img.title = match[1];
    img.referrerPolicy = "no-referrer";
    img.loading = "lazy";
    img.addEventListener("load", () => { if (S.atBottom) scrollThreadToBottom("smooth"); });
    el.appendChild(img);
    last = match.index + match[0].length;
    replaced = true;
  }
  if (!replaced) {
    el.textContent = text;
    return;
  }
  if (last < text.length) el.appendChild(document.createTextNode(text.slice(last)));
}

function insertEmoteText(text) {
  const input = $("input");
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  const caret = start + text.length;
  input.setSelectionRange(caret, caret);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
}

function toggleEmojiPanel(show) {
  const panel = $("emojiPanel");
  panel.hidden = !show;
  if (show && !S.emotes) loadEmotes();
}

async function loadEmotes() {
  try {
    S.emotes = await Api.get("emotes");
    buildEmoteMap();
    // 表情数据到手后，把已经渲染的纯文本气泡里的表情码换成图片
    refreshEmoteBubbles();
    renderBiliEmotes();
    renderLocalStickers();
    if (S.emotes.error) $("emojiSearch").title = S.emotes.error;
  } catch (err) {
    $("stickerTip").textContent = `表情加载失败：${err.message}`;
  }
}

// 只重画含表情码的文本气泡，不动图片/视频卡片，也不重置滚动位置
function refreshEmoteBubbles() {
  if (!S.emoteMap || !S.emoteMap.size) return;
  for (const wrap of $("thread").querySelectorAll(".bubble:not(.card-bubble)")) {
    const msg = S.messages.find((m) => String(m.id) === wrap.dataset.id);
    if (!msg || msg.kind === "image" || !/\[[^\[\]]{1,32}\]/.test(msg.text || "")) continue;
    wrap.textContent = "";
    renderTextWithEmotes(wrap, msg.text);
  }
}

function renderBiliEmotes() {
  const box = $("biliEmotes");
  const keyword = ($("emojiSearch").value || "").trim().toLowerCase();
  box.innerHTML = "";
  let shown = 0;
  for (const pkg of (S.emotes && S.emotes.packages) || []) {
    for (const item of pkg.items || []) {
      const hay = `${item.text} ${item.alias || ""} ${pkg.name}`.toLowerCase();
      if (keyword && !hay.includes(keyword)) continue;
      if (shown++ >= 300) break;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "emoji-item";
      btn.title = item.text;
      if (item.url) {
        const img = document.createElement("img");
        img.src = item.url.replace(/^http:/i, "https:");
        img.referrerPolicy = "no-referrer";
        img.alt = item.text;
        btn.appendChild(img);
      } else {
        const span = document.createElement("span");
        span.className = "emoji-text";
        span.textContent = item.text;
        btn.appendChild(span);
      }
      btn.addEventListener("click", () => insertEmoteText(item.text));
      box.appendChild(btn);
    }
  }
  if (!box.children.length) {
    box.textContent = "没有匹配的表情";
  }
}

function renderLocalStickers() {
  const box = $("localStickers");
  const items = (S.emotes && S.emotes.stickers) || [];
  box.innerHTML = "";
  $("stickerTip").textContent = items.length
    ? "点图片直接发送 · 右上角 × 删除"
    : "还没有本地表情，点上面导入 jpg / gif";
  for (const item of items) {
    const tile = document.createElement("div");
    tile.className = "sticker-item";
    if (item.data) {
      const img = document.createElement("img");
      img.src = `data:${item.mime};base64,${item.data}`;
      img.alt = item.name;
      img.title = item.name;
      img.addEventListener("click", () => sendSticker(item.id));
      tile.appendChild(img);
    } else {
      tile.textContent = item.name;
    }
    const del = document.createElement("span");
    del.className = "sticker-del";
    del.textContent = "×";
    del.title = `删除 ${item.name}`;
    del.addEventListener("click", async () => {
      try {
        await Api.post("sticker/delete", { id: item.id });
        await loadEmotes();
      } catch (err) {
        $("stickerTip").textContent = err.message;
      }
    });
    tile.appendChild(del);
    box.appendChild(tile);
  }
}

async function sendSticker(id) {
  if (!S.active) {
    $("stickerTip").textContent = "先选一个会话";
    return;
  }
  try {
    const res = await Api.post("sticker/send", { peer: S.active, id });
    toggleEmojiPanel(false);
    if (res.message && res.message.id) {
      syncMessages([...S.messages, res.message]);
    } else {
      await loadMessages(true);
    }
    log("out", "发了一个图片表情");
  } catch (err) {
    $("stickerTip").textContent = `发送失败：${err.message}`;
  }
}

async function importStickers(files) {
  if (!files || !files.length) return;
  $("stickerTip").textContent = `正在导入 ${files.length} 张…`;
  let ok = 0;
  for (const file of files) {
    const ext = String(file.name || "").split(".").pop()?.toLowerCase() || "";
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("读取文件失败"));
        reader.readAsDataURL(file);
      });
      const data = dataUrl.split(",")[1] || "";
      await Api.post("sticker/add", {
        name: file.name.replace(/\.[^.]+$/, ""),
        data,
        ext,
      });
      ok += 1;
    } catch (err) {
      $("stickerTip").textContent = `${file.name} 导入失败：${err.message}`;
    }
  }
  $("stickerTip").textContent = `已导入 ${ok} 张`;
  await loadEmotes();
}

/* ---------------- 设置 ---------------- */
function openModal() {
  const cfg = S.state?.config || {};
  document.querySelectorAll('input[name="scope"]').forEach((r) => {
    r.checked = r.value === (cfg.scope || "all");
  });
  $("peerInput").value = cfg.peer_uid || "";
  $("intervalInput").value = cfg.panel_refresh || 10;
  $("paletteSync").checked = cfg.palette_sync !== false;
  $("paletteHint").textContent = paletteStatusText(S.palette);
  $("modalMask").hidden = false;
}

function closeModal() {
  $("modalMask").hidden = true;
}

async function saveScope() {
  const scope = document.querySelector('input[name="scope"]:checked')?.value || "all";
  const peer = Number($("peerInput").value || 0);
  const interval = Number($("intervalInput").value || 10);
  const paletteSync = $("paletteSync").checked;
  if (scope === "single" && !peer) {
    $("sendError").textContent = "选择「只显示指定的人」时要填 UID";
    return;
  }
  try {
    await Api.post("config", {
      scope, peer_uid: peer, panel_refresh: interval, palette_sync: paletteSync,
    });
    closeModal();
    await loadState();
    await refreshPalette();
    await loadSessions();
    if (scope === "single" && peer) {
      await openSession(peer);
    } else if (!S.sessions.some((s) => s.peer_uid === S.active)) {
      S.active = 0;
      renderMessages();
      renderSessions();
    }
    log("sys", scope === "single" ? `只看 ${peer}` : "显示所有会话");
  } catch (err) {
    $("sendError").textContent = err.message;
  }
}

async function loadState() {
  const data = await Api.get("state");
  renderState(data);
  return data;
}

/* ---------------- 绑定 ---------------- */
function bind() {
  $("thread").addEventListener(
    "scroll",
    () => {
      const t = $("thread");
      S.atBottom = t.scrollHeight - t.scrollTop - t.clientHeight < 120;
    },
    { passive: true }
  );
  $("btnSend").addEventListener("click", send);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });
  $("input").addEventListener("input", () => {
    autoGrow();
    $("count").textContent = String($("input").value.length);
  });

  $("search").addEventListener("input", renderSessions);
  $("btnEmoji").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleEmojiPanel($("emojiPanel").hidden);
  });
  $("btnCloseEmoji").addEventListener("click", () => toggleEmojiPanel(false));
  $("emojiSearch").addEventListener("input", renderBiliEmotes);
  document.querySelectorAll(".emoji-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".emoji-tab").forEach((t) => {
        t.classList.toggle("active", t === tab);
      });
      document.querySelectorAll(".emoji-body").forEach((body) => {
        body.hidden = body.dataset.body !== tab.dataset.tab;
      });
    });
  });
  $("btnImportSticker").addEventListener("click", () => $("stickerFile").click());
  $("stickerFile").addEventListener("change", (e) => {
    importStickers(e.target.files);
    e.target.value = "";
  });
  document.addEventListener("click", (e) => {
    const panel = $("emojiPanel");
    if (!panel.hidden && !panel.contains(e.target) && e.target !== $("btnEmoji")) {
      toggleEmojiPanel(false);
    }
  });
  $("btnSettings").addEventListener("click", openModal);
  $("btnPeerSettings").addEventListener("click", openModal);
  $("btnCloseModal").addEventListener("click", closeModal);
  $("btnSaveScope").addEventListener("click", saveScope);
  $("modalMask").addEventListener("click", (e) => {
    if (e.target === $("modalMask")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("modalMask").hidden) closeModal();
  });

  $("btnRefreshSessions").addEventListener("click", async () => {
    try {
      const res = await Api.post("refresh", {});
      log("sys", `会话已刷新（${res.sessions ?? 0}）`);
      await loadSessions();
      if (S.active) await loadMessages(true);
    } catch (err) {
      log("err", err.message);
      $("sendError").textContent = err.message;
    }
  });

  $("btnClearLog").addEventListener("click", () => { $("log").innerHTML = ""; });
}

/* ---------------- 调色盘适配 ----------------
   调色盘（astrbot_plugin_palette）的样式是注入 Dashboard 的 index.html 的，
   插件视图是 sandbox iframe，既收不到注入也读不到父页面的 CSS 变量。
   所以后端把它的配置读出来，面板这边照着复刻同一套观感。
*/
const clampNum = (v, low, high) => Math.min(high, Math.max(low, v));

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function shade(rgb, amount) {
  const target = amount < 0 ? 0 : 255;
  const ratio = Math.abs(amount);
  return rgb.map((c) => Math.round(c + (target - c) * ratio));
}

const rgba = (rgb, alpha) => `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;

function paletteVars(p) {
  const root = document.documentElement;
  const dark = root.dataset.theme === "dark";
  const surface = dark ? [26, 28, 33] : [255, 255, 255];
  const onSurface = dark ? [232, 235, 242] : [24, 32, 45];
  const opacity = clampNum(Number(p.surface_opacity) || 0, 0, 1);
  const primary = hexToRgb(p.primary) || (dark ? [92, 142, 212] : [50, 88, 147]);

  // 与调色盘 glass.py 的取值同源（0.42/0.54 打底，随界面透明度上浮）；
  // 聊天内容比信息卡片更需要对比度，所以承载文字的表面再抬 0.06
  root.style.setProperty("--pal-soft", rgba(surface, clampNum(0.42 + opacity * 0.32, 0.36, 0.9)));
  root.style.setProperty("--pal-strong", rgba(surface, clampNum(0.6 + opacity * 0.26, 0.52, 0.95)));
  root.style.setProperty("--pal-line", rgba(onSurface, dark ? 0.18 : 0.1));
  // 壁纸上的次级文字要比实底界面更实一点，否则灰字会糊掉
  root.style.setProperty("--pal-muted", rgba(onSurface, dark ? 0.74 : 0.62));
  root.style.setProperty("--pal-text-2", rgba(onSurface, dark ? 0.88 : 0.78));
  root.style.setProperty("--pal-blur", `${clampNum(Number(p.glass_blur) || 0, 0, 40)}px`);
  root.style.setProperty("--pal-accent", `rgb(${primary.join(", ")})`);
  root.style.setProperty("--pal-accent-rgb", primary.join(", "));
  root.style.setProperty("--pal-accent-deep", `rgb(${shade(primary, -0.18).join(", ")})`);
  root.style.setProperty("--pal-accent-soft", rgba(primary, dark ? 0.26 : 0.13));

  const enh = p.text_enhancement || {};
  const strength = clampNum(Number(enh.strength) || 0, 0, 2);
  let shadow = "none";
  if (enh.mode === "soft_shadow" && strength > 0) {
    shadow = `0 1px 2px rgba(0,0,0,${(0.26 * strength).toFixed(3)}),`
      + ` 0 0 10px rgba(0,0,0,${(0.16 * strength).toFixed(3)})`;
  } else if (enh.mode === "stroke" && strength > 0) {
    shadow = `0 0 1px rgba(0,0,0,${(0.5 * strength).toFixed(3)}),`
      + ` 0 1px 2px rgba(0,0,0,${(0.42 * strength).toFixed(3)}),`
      + ` 0 0 6px rgba(0,0,0,${(0.28 * strength).toFixed(3)})`;
  }
  root.style.setProperty("--pal-text-shadow", shadow);
}

function applyPalette(p) {
  const root = document.documentElement;
  S.palette = p || null;
  const cfg = (S.state && S.state.config) || {};
  const on = Boolean(cfg.palette_sync !== false && p && p.available && p.enabled);
  root.dataset.palette = on ? "on" : "off";
  if (on) paletteVars(p);
}

function paletteStatusText(p) {
  const cfg = (S.state && S.state.config) || {};
  if (cfg.palette_sync === false) return "已关闭：面板用自己的配色";
  if (!p) return "还没读到调色盘状态";
  if (!p.available) return p.reason || "未检测到 AstrBot 调色盘";
  if (!p.enabled) return "检测到调色盘，但它当前是关闭状态";
  const bits = [`主色 ${p.primary || "默认"}`];
  bits.push(p.glass_blur > 0 ? `毛玻璃 ${p.glass_blur}px` : "无毛玻璃");
  if (p.text_enhancement && p.text_enhancement.mode !== "off") bits.push("文字增强");
  return `已跟随调色盘：${bits.join(" · ")}`;
}

async function refreshPalette() {
  try {
    const p = await Api.get("theme");
    applyPalette(p);
    const hint = $("paletteHint");
    if (hint) hint.textContent = paletteStatusText(p);
    return p;
  } catch (_) {
    return null;
  }
}

/* ---------------- 启动 ---------------- */
async function boot() {
  if (bridge) {
    try { await bridge.ready(); } catch (_) { /* 独立打开时忽略 */ }
    document.body.classList.add("in-astrbot");
    // Dashboard 切换明暗主题时，玻璃表面的底色要跟着重算
    if (typeof bridge.onContext === "function") {
      bridge.onContext(() => { if (S.palette) applyPalette(S.palette); });
    }
  } else if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const syncTheme = () => {
      document.documentElement.dataset.theme = mq.matches ? "dark" : "light";
      if (S.palette) applyPalette(S.palette);
    };
    if (mq.addEventListener) mq.addEventListener("change", syncTheme);
    syncTheme();
  }
  bind();

  try {
    await loadState();
    // 静默拉一次表情：历史消息里的表情码也要渲染成图片，不必等用户点开面板
    void loadEmotes();
    await refreshPalette();
    await loadSessions();
    if (new URLSearchParams(location.search).get("nosse") !== "1") {
      subscribeEvents(handleEvent);
    } else {
      log("sys", "已跳过实时流（nosse=1）");
    }
    const cfg = S.state?.config || {};
    if (cfg.scope === "single" && cfg.peer_uid) {
      await openSession(cfg.peer_uid);
    } else if (S.sessions.length) {
      await openSession(S.sessions[0].peer_uid);
    } else {
      renderMessages();
    }
    log("sys", MODE === "astrbot" ? "已接入 AstrBot 面板" : "面板已就绪");
  } catch (err) {
    log("err", err.message);
    renderMessages();
  }
}

boot();
