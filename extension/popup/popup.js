/**
 * Fetchr Popup — main controller
 *
 * Connects to the local agent via WebSocket for live updates.
 * Falls back to polling if WS is unavailable.
 */

const AGENT_BASE = "http://127.0.0.1:9876";
const WS_URL     = "ws://127.0.0.1:9876/ws";

// ------------------------------------------------------------------ //
// DOM refs                                                            //
// ------------------------------------------------------------------ //

const $  = (id) => document.getElementById(id);

const agentDot        = $("agentDot");
const agentLabel      = $("agentLabel");
const activeCount     = $("activeCount");
const totalSpeedEl    = $("totalSpeed");
const emptyState      = $("emptyState");
const interceptStatus = $("interceptStatus");
const downloadList    = $("downloadList");
const mediaPanel      = $("mediaPanel");
const mediaList       = $("mediaList");
const urlInputWrap    = $("urlInputWrap");
const urlInput        = $("urlInput");

// ------------------------------------------------------------------ //
// State                                                               //
// ------------------------------------------------------------------ //

let downloads = {};   // id → download object
let ws        = null;
let wsRetries = 0;
const MAX_RETRIES = 8;

// ------------------------------------------------------------------ //
// Startup                                                             //
// ------------------------------------------------------------------ //

document.addEventListener("DOMContentLoaded", () => {
  initUI();
  connectWS();
  loadInterceptState();
});

// ------------------------------------------------------------------ //
// WebSocket                                                           //
// ------------------------------------------------------------------ //

function connectWS() {
  if (ws) { try { ws.close(); } catch {} }

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    wsRetries = 0;
    setAgentStatus(true);
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "snapshot") {
        downloads = {};
        msg.data.forEach((d) => { downloads[d.id] = d; });
        renderAll();
      } else if (msg.type === "progress") {
        downloads[msg.data.id] = msg.data;
        renderItem(msg.data);
        updateStats();
      }
    } catch {}
  };

  ws.onerror  = () => { setAgentStatus(false); };
  ws.onclose  = () => {
    setAgentStatus(false);
    if (wsRetries < MAX_RETRIES) {
      const delay = Math.min(1000 * 2 ** wsRetries, 15000);
      wsRetries++;
      setTimeout(connectWS, delay);
    }
  };
}

// ------------------------------------------------------------------ //
// Agent status                                                        //
// ------------------------------------------------------------------ //

function setAgentStatus(online) {
  agentDot.className  = "agent-dot " + (online ? "online" : "offline");
  agentLabel.textContent = online ? "Agent online" : "Agent offline";
}

// ------------------------------------------------------------------ //
// Render all downloads                                                //
// ------------------------------------------------------------------ //

function renderAll() {
  // Remove existing dl-item nodes (keep #emptyState)
  [...downloadList.querySelectorAll(".dl-item")].forEach((n) => n.remove());

  const items = Object.values(downloads).sort(
    (a, b) => new Date(b.added_at) - new Date(a.added_at)
  );

  emptyState.classList.toggle("hidden", items.length > 0);
  items.forEach((d) => renderItem(d));
  updateStats();
}

// ------------------------------------------------------------------ //
// Render / update a single download item                             //
// ------------------------------------------------------------------ //

function renderItem(d) {
  let el = document.getElementById(`dl-${d.id}`);
  if (!el) {
    el = createItemElement(d);
    // insert before emptyState or at end
    downloadList.insertBefore(el, emptyState);
  }
  updateItemElement(el, d);
  emptyState.classList.add("hidden");
}

function createItemElement(d) {
  const el = document.createElement("div");
  el.className = "dl-item";
  el.id        = `dl-${d.id}`;
  el.innerHTML = `
    <div class="dl-icon ${iconClass(d.filename)}">${iconLabel(d.filename)}</div>
    <div class="dl-name" title="${esc(d.filename)}">${esc(d.filename)}</div>
    <div class="dl-controls">
      <button class="ctrl-btn pause-btn" title="Pause/Resume">
        ${pauseIcon()}
      </button>
      <button class="ctrl-btn danger cancel-btn" title="Cancel & Remove">
        ${cancelIcon()}
      </button>
    </div>
    <div class="dl-progress-wrap">
      <div class="progress-bar"><div class="progress-fill"></div></div>
      <span class="progress-pct">0%</span>
    </div>
    <div class="dl-meta">
      <span class="status-badge"></span>
      <span class="speed-el"></span>
      <span class="size-el"></span>
      <span class="eta-el"></span>
    </div>
  `;

  el.querySelector(".pause-btn").addEventListener("click", () => {
    const item = downloads[d.id];
    if (!item) return;
    const action = item.status === "paused" ? "resumeDownload" : "pauseDownload";
    chrome.runtime.sendMessage({ type: action, id: d.id });
  });

  el.querySelector(".cancel-btn").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "cancelDownload", id: d.id });
    delete downloads[d.id];
    el.remove();
    if (!downloadList.querySelector(".dl-item")) {
      emptyState.classList.remove("hidden");
    }
    updateStats();
  });

  return el;
}

function updateItemElement(el, d) {
  const fill    = el.querySelector(".progress-fill");
  const pct     = el.querySelector(".progress-pct");
  const badge   = el.querySelector(".status-badge");
  const speedEl = el.querySelector(".speed-el");
  const sizeEl  = el.querySelector(".size-el");
  const etaEl   = el.querySelector(".eta-el");
  const pauseBtn = el.querySelector(".pause-btn");

  const p = Math.min(d.progress || 0, 100);
  fill.style.width = p + "%";
  fill.className   = `progress-fill ${fillClass(d.status)}`;
  pct.textContent  = p.toFixed(0) + "%";

  badge.className   = `status-badge status-${d.status}`;
  badge.textContent = d.status;

  speedEl.textContent = d.status === "active" && d.speed > 0
    ? fmtSpeed(d.speed)
    : "";

  sizeEl.textContent  = d.total > 0
    ? `${fmtBytes(d.downloaded)} / ${fmtBytes(d.total)}`
    : d.downloaded > 0 ? fmtBytes(d.downloaded) : "";

  // ETA + segment count badge
  const segs = d.segments || 1;
  const segLabel = d.status === "active" && segs > 1 ? ` · ⚡${segs} streams` : "";

  etaEl.textContent = d.status === "active" && d.eta > 0
    ? `ETA ${fmtEta(d.eta)}${segLabel}`
    : (d.status === "active" && segs > 1 ? `⚡ ${segs} parallel streams` : "");

  if (d.error) etaEl.textContent = "⚠ " + d.error.slice(0, 40);

  // toggle pause button icon
  pauseBtn.innerHTML = d.status === "paused" ? resumeIcon() : pauseIcon();
  pauseBtn.title     = d.status === "paused" ? "Resume" : "Pause";

  // hide pause/resume for terminal states
  const terminal = ["complete", "error", "cancelled"].includes(d.status);
  pauseBtn.style.display = terminal ? "none" : "";
}

// ------------------------------------------------------------------ //
// Stats bar                                                           //
// ------------------------------------------------------------------ //

function updateStats() {
  const items  = Object.values(downloads);
  const active = items.filter((d) => d.status === "active").length;
  const speed  = items.reduce((s, d) => s + (d.speed || 0), 0);
  activeCount.textContent   = active;
  totalSpeedEl.textContent  = speed > 0 ? fmtSpeed(speed) : "0 B/s";
}

// ------------------------------------------------------------------ //
// UI event wiring                                                     //
// ------------------------------------------------------------------ //

function initUI() {
  $("settingsBtn").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
  });

  $("clearFinishedBtn").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "clearFinished" });
    // Optimistically remove from local state
    Object.keys(downloads).forEach((id) => {
      const d = downloads[id];
      if (["complete", "cancelled", "error"].includes(d.status)) {
        delete downloads[id];
        document.getElementById(`dl-${id}`)?.remove();
      }
    });
    if (!downloadList.querySelector(".dl-item")) {
      emptyState.classList.remove("hidden");
    }
    updateStats();
  });

  // Grab media from page
  $("grabMediaBtn").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return;

    // For known media sites (YouTube, Vimeo, etc.) send the page URL
    // directly to the agent as a media download — no DOM scan needed
    const knownMediaHosts = [
      "youtube.com","youtu.be","vimeo.com","twitter.com","x.com",
      "instagram.com","tiktok.com","reddit.com","v.redd.it",
      "twitch.tv","dailymotion.com","facebook.com","soundcloud.com",
    ];
    try {
      const host = new URL(tab.url).hostname.replace(/^www\./, "");
      if (knownMediaHosts.some((h) => host === h || host.endsWith("." + h))) {
        chrome.runtime.sendMessage({
          type: "addDownload",
          payload: { url: tab.url, dl_type: "media" },
        });
        // Show a brief confirmation instead of opening the panel
        $("grabMediaBtn").textContent = "✓ Queued!";
        setTimeout(() => { $("grabMediaBtn").innerHTML = `<svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 6a2 2 0 012-2h6a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V6zm14-2a1 1 0 011 1v10a1 1 0 01-1.447.894L11 13.764V6.236l4.553-2.13A1 1 0 0116 4z"/></svg> Grab media`; }, 2000);
        return;
      }
    } catch {}

    // For other pages: scan DOM for embedded media
    chrome.runtime.sendMessage({ type: "scanPage", tabId: tab.id });
    mediaPanel.classList.remove("hidden");
    mediaList.innerHTML = `<div style="padding:10px 14px;color:var(--sub);font-size:11.5px">Scanning…</div>`;
  });

  // Listen for scanned media from background
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "pageMedia") {
      renderMediaPanel(msg.items);
    }
    if (msg.type === "agentStatus") {
      setAgentStatus(msg.online);
    }
  });

  $("closePanelBtn").addEventListener("click", () => {
    mediaPanel.classList.add("hidden");
  });

  // Batch select
  $("batchSelectBtn").addEventListener("click", async () => {
    const btn = $("batchSelectBtn");
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return;

    if (btn.classList.contains("active-mode")) {
      chrome.tabs.sendMessage(tab.id, { type: "stopBatchSelect" });
      btn.classList.remove("active-mode");
      btn.title = "Batch select";
    } else {
      chrome.tabs.sendMessage(tab.id, { type: "startBatchSelect" });
      btn.classList.add("active-mode");
      btn.title = "Stop batch select";
      window.close();  // close popup so user can click on page
    }
  });

  // Paste URL
  $("pasteUrlBtn").addEventListener("click", async () => {
    urlInputWrap.classList.toggle("hidden");
    if (!urlInputWrap.classList.contains("hidden")) {
      try {
        const text = await navigator.clipboard.readText();
        if (text.startsWith("http")) urlInput.value = text;
      } catch {}
      urlInput.focus();
    }
  });

  $("dlGoBtn").addEventListener("click", submitUrlDownload);
  urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitUrlDownload();
  });
}

async function submitUrlDownload() {
  const url = urlInput.value.trim();
  if (!url) return;
  const type = document.querySelector("input[name='dltype']:checked").value;
  chrome.runtime.sendMessage({
    type: "addDownload",
    payload: { url, dl_type: type },
  });
  urlInput.value = "";
  urlInputWrap.classList.add("hidden");
}

// ------------------------------------------------------------------ //
// Media panel renderer                                                //
// ------------------------------------------------------------------ //

function renderMediaPanel(items) {
  if (!items || items.length === 0) {
    mediaList.innerHTML = `<div style="padding:10px 14px;color:var(--sub);font-size:11.5px">No media found on this page.</div>`;
    return;
  }
  mediaList.innerHTML = "";
  items.slice(0, 50).forEach((item) => {
    const row = document.createElement("div");
    row.className = "media-item";
    row.innerHTML = `
      <span class="media-type-badge badge-${item.type}">${esc(item.type)}</span>
      <span class="media-label" title="${esc(item.url)}">${esc(item.label || item.url.split("/").pop())}</span>
      <button class="media-dl-btn">↓</button>
    `;
    row.querySelector(".media-dl-btn").addEventListener("click", () => {
      const dlType = ["video", "audio", "source", "og:video"].includes(item.type)
        ? "media"
        : "direct";
      chrome.runtime.sendMessage({
        type: "addDownload",
        payload: { url: item.url, dl_type: dlType },
      });
      row.querySelector(".media-dl-btn").textContent = "✓";
      row.querySelector(".media-dl-btn").disabled = true;
    });
    mediaList.appendChild(row);
  });
}

// ------------------------------------------------------------------ //
// Intercept state                                                     //
// ------------------------------------------------------------------ //

async function loadInterceptState() {
  const { interceptAll } = await chrome.storage.sync.get({ interceptAll: true });
  interceptStatus.textContent = interceptAll ? "active" : "paused";
}

// ------------------------------------------------------------------ //
// Helpers                                                             //
// ------------------------------------------------------------------ //

function fmtBytes(b) {
  if (!b) return "0 B";
  const units = ["B","KB","MB","GB","TB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function fmtSpeed(bps) {
  return fmtBytes(bps) + "/s";
}

function fmtEta(secs) {
  if (secs < 60)  return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs/60)}m ${secs%60}s`;
  return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
}

function fillClass(status) {
  const map = { complete:"complete", error:"error", paused:"paused", cancelled:"cancelled" };
  return map[status] || "";
}

function iconClass(filename) {
  const ext = (filename || "").split(".").pop().toLowerCase();
  if (["mp4","mkv","webm","avi","mov","flv"].includes(ext)) return "icon-video";
  if (["mp3","m4a","flac","wav","ogg","opus"].includes(ext)) return "icon-audio";
  if (["jpg","jpeg","png","gif","webp","svg","bmp"].includes(ext)) return "icon-image";
  if (["pdf","doc","docx","txt","md"].includes(ext)) return "icon-doc";
  if (["zip","rar","7z","tar","gz","iso"].includes(ext)) return "icon-arc";
  return "icon-file";
}

function iconLabel(filename) {
  const ext = (filename || "").split(".").pop().toLowerCase();
  if (["mp4","mkv","webm","avi","mov","flv"].includes(ext)) return "VID";
  if (["mp3","m4a","flac","wav","ogg","opus"].includes(ext)) return "AUD";
  if (["jpg","jpeg","png","gif","webp","svg"].includes(ext)) return "IMG";
  if (["pdf","doc","docx","txt","md"].includes(ext)) return "DOC";
  if (["zip","rar","7z","tar","gz","iso"].includes(ext)) return "ARC";
  return ext.slice(0,3).toUpperCase() || "?";
}

function esc(str) {
  return String(str || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function pauseIcon() {
  return `<svg viewBox="0 0 12 12" fill="currentColor">
    <rect x="2" y="2" width="3" height="8" rx="1"/>
    <rect x="7" y="2" width="3" height="8" rx="1"/>
  </svg>`;
}

function resumeIcon() {
  return `<svg viewBox="0 0 12 12" fill="currentColor">
    <path d="M3 2l7 4-7 4V2z"/>
  </svg>`;
}

function cancelIcon() {
  return `<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round">
    <line x1="3" y1="3" x2="9" y2="9"/>
    <line x1="9" y1="3" x2="3" y2="9"/>
  </svg>`;
}
