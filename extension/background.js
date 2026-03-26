/**
 * Fetchr Background Service Worker
 *
 * Responsibilities:
 *  - Maintain connection to local agent (health-check polling)
 *  - Intercept chrome.downloads events and redirect to agent
 *  - Handle messages from popup and content scripts
 *  - Manage context menu actions
 *  - Show notifications on completion/error
 */

const AGENT_BASE = "http://127.0.0.1:9876";
const HEALTH_INTERVAL_MS = 8_000;   // poll every 8 s
const INTERCEPT_KEY = "interceptAll";

// ------------------------------------------------------------------ //
// Media URL detection                                                 //
// Sites where yt-dlp should be used instead of direct HTTP           //
// ------------------------------------------------------------------ //

const MEDIA_HOSTS = [
  // Video platforms
  "youtube.com", "youtu.be",
  "vimeo.com", "dailymotion.com",
  "twitch.tv", "clips.twitch.tv",
  // Social
  "twitter.com", "x.com",
  "instagram.com", "facebook.com",
  "tiktok.com",
  "reddit.com", "v.redd.it",
  // Audio
  "soundcloud.com", "bandcamp.com",
  "mixcloud.com",
  // Messaging / social video (yt-dlp extracts; cookies from browser handle auth)
  "t.me", "telegram.me", "telegram.org",
  // Adult content — yt-dlp supports all of these natively
  "erome.com",
  "pornhub.com", "ph.pornhub.com",
  "xvideos.com", "xnxx.com", "xhamster.com",
  "redtube.com", "youporn.com", "tube8.com",
  "spankbang.com", "txxx.com", "hclips.com",
  "thisvid.com", "xfantasy.com",
  "rule34video.com", "rule34.xxx",
  // Other hosting
  "streamable.com", "streamff.com",
  "medal.tv", "gfycat.com",
  "bilibili.com",
];

const DIRECT_EXTS = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|dmg|pkg|deb|rpm|apk|ipa|pdf|docx?|xlsx?|pptx?|mp3|m4a|flac|wav|ogg|opus|mp4|mkv|webm|avi|mov|jpg|jpeg|png|gif|webp|svg)(\?.*)?$/i;

function detectDlType(url) {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");

    // Known media streaming sites → always use yt-dlp
    if (MEDIA_HOSTS.some((h) => host === h || host.endsWith("." + h))) {
      return "media";
    }

    // Direct file extension → HTTP download
    if (DIRECT_EXTS.test(u.pathname)) {
      return "direct";
    }

    // Fallback: treat as direct
    return "direct";
  } catch {
    return "direct";
  }
}

// ------------------------------------------------------------------ //
// State                                                               //
// ------------------------------------------------------------------ //

let agentOnline = false;

// ------------------------------------------------------------------ //
// Agent health check                                                  //
// ------------------------------------------------------------------ //

async function checkAgent() {
  try {
    const r = await fetch(`${AGENT_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    agentOnline = r.ok;
  } catch {
    agentOnline = false;
  }
  // Broadcast status to any open popups
  chrome.runtime.sendMessage({ type: "agentStatus", online: agentOnline }).catch(() => {});
}

// Run immediately + on interval
checkAgent();
setInterval(checkAgent, HEALTH_INTERVAL_MS);

// ------------------------------------------------------------------ //
// Download interception                                               //
// ------------------------------------------------------------------ //

async function shouldIntercept() {
  const { interceptAll } = await chrome.storage.sync.get({ interceptAll: true });
  return interceptAll && agentOnline;
}

chrome.downloads.onCreated.addListener(async (downloadItem) => {
  if (!(await shouldIntercept())) return;

  // Cancel the browser-native download
  chrome.downloads.cancel(downloadItem.id);
  chrome.downloads.erase({ id: downloadItem.id });

  const dlType = detectDlType(downloadItem.url);

  // For media URLs the filename will be resolved by yt-dlp — don't guess
  const filename = dlType === "direct" && downloadItem.filename
    ? downloadItem.filename.split("/").pop()
    : undefined;

  // Capture referrer — the page that triggered the download.
  // Sites like EroMe, Pornhub, etc. block requests without a matching Referer.
  const referer = downloadItem.referrer || downloadItem.initiator || "";

  // Grab cookies for the download URL's domain from Chrome's cookie store
  // so protected/authenticated sites get a valid session.
  let cookieHeader = "";
  try {
    const url    = new URL(downloadItem.url);
    const domain = url.hostname;
    const cookies = await chrome.cookies.getAll({ domain });
    if (cookies.length > 0) {
      cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    }
  } catch {}

  await sendToAgent({
    url:     downloadItem.url,
    filename,
    dl_type: dlType,
    referer,
    cookies: cookieHeader,
  });
});

// ------------------------------------------------------------------ //
// Send download to agent                                               //
// ------------------------------------------------------------------ //

async function sendToAgent(payload) {
  try {
    const r = await fetch(`${AGENT_BASE}/downloads`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`Agent error ${r.status}`);
    const item = await r.json();

    chrome.notifications.create({
      type:     "basic",
      iconUrl:  "icons/icon48.png",
      title:    "Fetchr — Added",
      message:  item.filename || payload.url,
      priority: 0,
    });

    return item;
  } catch (err) {
    chrome.notifications.create({
      type:     "basic",
      iconUrl:  "icons/icon48.png",
      title:    "Fetchr — Error",
      message:  `Could not reach agent: ${err.message}`,
      priority: 1,
    });
    return null;
  }
}

// ------------------------------------------------------------------ //
// Context menus                                                       //
// ------------------------------------------------------------------ //

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id:       "fetchr-download-link",
      title:    "Download with Fetchr",
      contexts: ["link"],
    });
    chrome.contextMenus.create({
      id:       "fetchr-grab-media",
      title:    "Grab media with Fetchr",
      contexts: ["page", "video", "audio"],
    });
    chrome.contextMenus.create({
      id:       "fetchr-grab-image",
      title:    "Save image with Fetchr",
      contexts: ["image"],
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const pageUrl = info.pageUrl || tab?.url || "";

  if (info.menuItemId === "fetchr-download-link" && info.linkUrl) {
    const dlType = detectDlType(info.linkUrl);
    const cookies = await getTabCookies(info.linkUrl);
    await sendToAgent({ url: info.linkUrl, dl_type: dlType, referer: pageUrl, cookies });
  }

  if (info.menuItemId === "fetchr-grab-media" && info.pageUrl) {
    await sendToAgent({ url: info.pageUrl, dl_type: "media", referer: pageUrl });
  }

  if (info.menuItemId === "fetchr-grab-image" && info.srcUrl) {
    const cookies = await getTabCookies(info.srcUrl);
    await sendToAgent({ url: info.srcUrl, dl_type: "direct", referer: pageUrl, cookies });
  }
});

// Helper: build a Cookie header string for a given URL
async function getTabCookies(url) {
  try {
    const domain  = new URL(url).hostname;
    const cookies = await chrome.cookies.getAll({ domain });
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch {
    return "";
  }
}

// ------------------------------------------------------------------ //
// Message handler (from popup / content script)                      //
// ------------------------------------------------------------------ //

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.type) {

      // Popup / options asks for agent status (avoids direct localhost fetch from page context)
      case "getAgentStatus":
        sendResponse({ online: agentOnline });
        break;

      // Options page asks for agent settings via SW (bypasses Private Network Access restriction)
      case "getAgentSettings": {
        try {
          const r = await fetch(`${AGENT_BASE}/settings`, { signal: AbortSignal.timeout(4000) });
          const s = r.ok ? await r.json() : null;
          sendResponse({ online: r.ok, settings: s });
        } catch {
          sendResponse({ online: false, settings: null });
        }
        break;
      }

      // Options page saves agent settings via SW
      case "putAgentSettings": {
        try {
          const r = await fetch(`${AGENT_BASE}/settings`, {
            method:  "PUT",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify(msg.payload),
            signal:  AbortSignal.timeout(4000),
          });
          sendResponse({ ok: r.ok });
        } catch {
          sendResponse({ ok: false });
        }
        break;
      }

      // Popup / content asks to add a download
      case "addDownload": {
        const payload = { ...msg.payload };
        // Auto-attach cookies for the download URL if not already provided
        if (!payload.cookies) {
          payload.cookies = await getTabCookies(payload.url);
        }
        await sendToAgent(payload);
        sendResponse({ ok: true });
        break;
      }

      // Popup asks to pause/resume/cancel
      case "pauseDownload":
        await agentAction("pause", msg.id);
        sendResponse({ ok: true });
        break;

      case "resumeDownload":
        await agentAction("resume", msg.id);
        sendResponse({ ok: true });
        break;

      case "cancelDownload":
        await agentAction("cancel", msg.id);
        sendResponse({ ok: true });
        break;

      case "clearFinished":
        await fetch(`${AGENT_BASE}/downloads/clear-finished`, { method: "POST" });
        sendResponse({ ok: true });
        break;

      // Content script sends back media it found on the page
      case "pageMediaFound":
        // Forward to popup if open
        chrome.runtime.sendMessage({ type: "pageMedia", items: msg.items }).catch(() => {});
        sendResponse({ ok: true });
        break;

      // Popup requests a page scan
      case "scanPage":
        chrome.tabs.sendMessage(msg.tabId, { type: "scanPage" });
        sendResponse({ ok: true });
        break;

      // Batch download request from content selection UI
      case "batchDownload":
        for (const url of msg.urls) {
          await sendToAgent({ url, dl_type: "direct" });
        }
        sendResponse({ ok: true, count: msg.urls.length });
        break;

      default:
        sendResponse({ ok: false, error: "Unknown message type" });
    }
  })();
  return true;  // keep channel open for async sendResponse
});

// ------------------------------------------------------------------ //
// Helper: POST to agent action endpoints                              //
// ------------------------------------------------------------------ //

async function agentAction(action, id) {
  if (action === "cancel") {
    return fetch(`${AGENT_BASE}/downloads/${id}`, { method: "DELETE" });
  }
  return fetch(`${AGENT_BASE}/downloads/${id}/${action}`, { method: "POST" });
}
