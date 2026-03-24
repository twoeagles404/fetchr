/**
 * Fetchr Content Script
 *
 * Runs on every page. Provides:
 *  1. Video element watcher  — detects playing videos, shows IDM-style floating bar
 *  2. Media scanner          — finds video/audio/stream URLs on the page
 *  3. Batch selector         — overlay UI to select links and images
 *
 * Communication: messages to/from background.js
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------- //
  // Shared styles (injected once)                                     //
  // ---------------------------------------------------------------- //

  const ALL_STYLES = `
    /* ── Floating download bar ──────────────────────────────── */
    #fetchr-float-bar {
      position: fixed;
      bottom: 20px; left: 50%; transform: translateX(-50%);
      z-index: 2147483647;
      background: #13141f;
      border: 1px solid #2d2f4a;
      border-radius: 14px;
      padding: 10px 14px;
      display: flex; align-items: center; gap: 12px;
      box-shadow: 0 8px 32px rgba(0,0,0,.7), 0 0 0 1px rgba(79,110,247,.2);
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 13px; color: #e2e8f0;
      min-width: 320px; max-width: 520px;
      animation: fetchr-slide-up .25s ease;
    }
    @keyframes fetchr-slide-up {
      from { opacity: 0; transform: translateX(-50%) translateY(14px); }
      to   { opacity: 1; transform: translateX(-50%) translateY(0); }
    }
    #fetchr-float-icon {
      width: 32px; height: 32px; border-radius: 8px;
      background: rgba(79,110,247,.18);
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; font-size: 16px;
    }
    #fetchr-float-info { flex: 1; min-width: 0; }
    #fetchr-float-title {
      font-weight: 600; font-size: 12.5px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      color: #e2e8f0;
    }
    #fetchr-float-sub { font-size: 11px; color: #64748b; margin-top: 1px; }
    #fetchr-float-dl {
      background: #4f6ef7; color: #fff; border: none;
      border-radius: 8px; padding: 7px 16px;
      font-size: 12px; font-weight: 700; cursor: pointer;
      white-space: nowrap; flex-shrink: 0;
      transition: background .15s;
    }
    #fetchr-float-dl:hover  { background: #6b82ff; }
    #fetchr-float-dl.queued { background: #22c55e; }
    #fetchr-float-close {
      background: transparent; border: none;
      color: #475569; cursor: pointer; font-size: 16px; padding: 2px 4px;
      flex-shrink: 0; line-height: 1; transition: color .15s;
    }
    #fetchr-float-close:hover { color: #e2e8f0; }
    #fetchr-float-segments {
      font-size: 10.5px; color: #4f6ef7; margin-top: 2px;
    }

    /* ── Batch selector ─────────────────────────────────────── */
    #fetchr-batch-overlay {
      position: fixed; inset: 0; z-index: 2147483647;
      background: rgba(0,0,0,.55); backdrop-filter: blur(2px);
      font-family: system-ui, sans-serif;
    }
    #fetchr-batch-panel {
      position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
      background: #1a1b2e; border: 1px solid #2d2f4a; border-radius: 12px;
      padding: 16px 20px; display: flex; align-items: center; gap: 14px;
      box-shadow: 0 8px 32px rgba(0,0,0,.6); color: #e2e8f0;
      font-size: 14px; min-width: 340px;
    }
    #fetchr-batch-count { font-weight: 600; color: #7c8cff; min-width: 100px; }
    #fetchr-batch-dl {
      background: #4f6ef7; color: #fff; border: none; border-radius: 8px;
      padding: 8px 18px; cursor: pointer; font-size: 14px; font-weight: 600;
    }
    #fetchr-batch-dl:hover { background: #6b82ff; }
    #fetchr-batch-cancel {
      background: transparent; color: #94a3b8; border: 1px solid #2d2f4a;
      border-radius: 8px; padding: 8px 14px; cursor: pointer; font-size: 14px;
    }
    #fetchr-batch-cancel:hover { color: #e2e8f0; }
    .fetchr-selectable {
      outline: 2px dashed rgba(79,110,247,.5) !important;
      cursor: crosshair !important;
    }
    .fetchr-selected {
      outline: 2px solid #4f6ef7 !important;
      box-shadow: 0 0 0 4px rgba(79,110,247,.25) !important;
    }
  `;

  function injectStyles() {
    if (document.getElementById("fetchr-styles")) return;
    const s = document.createElement("style");
    s.id = "fetchr-styles";
    s.textContent = ALL_STYLES;
    (document.head || document.documentElement).appendChild(s);
  }

  // ---------------------------------------------------------------- //
  // 1. Floating download bar                                          //
  // ---------------------------------------------------------------- //

  let floatBar    = null;
  let floatDismissTimer = null;
  const seenUrls  = new Set();   // avoid re-showing for same URL

  function showFloatingBar(info) {
    // De-duplicate: don't show again for the same URL in this page session
    const key = info.url.slice(0, 120);
    if (seenUrls.has(key)) return;
    seenUrls.add(key);

    injectStyles();
    removeFloatingBar();
    clearTimeout(floatDismissTimer);

    const isMSE    = info.dlType === "media";
    const title    = info.title || (isMSE ? document.title : info.url.split("/").pop());
    const subText  = isMSE
      ? "Video detected — yt-dlp will handle this"
      : (info.sizeFmt ? `${info.sizeFmt} · Parallel download ready` : "Click to download with Fetchr");

    floatBar = document.createElement("div");
    floatBar.id = "fetchr-float-bar";
    floatBar.innerHTML = `
      <div id="fetchr-float-icon">${isMSE ? "🎬" : "⬇"}</div>
      <div id="fetchr-float-info">
        <div id="fetchr-float-title" title="${escHtml(title)}">${escHtml(title.slice(0, 60))}</div>
        <div id="fetchr-float-sub">${escHtml(subText)}</div>
        ${!isMSE ? '<div id="fetchr-float-segments">⚡ Multi-segment download</div>' : ""}
      </div>
      <button id="fetchr-float-dl">⬇ Download</button>
      <button id="fetchr-float-close">✕</button>
    `;

    document.documentElement.appendChild(floatBar);

    floatBar.querySelector("#fetchr-float-dl").addEventListener("click", () => {
      chrome.runtime.sendMessage({
        type: "addDownload",
        payload: {
          url:     info.url,
          dl_type: info.dlType,
          referer: info.referer || window.location.href,
        },
      });
      const btn = floatBar?.querySelector("#fetchr-float-dl");
      if (btn) { btn.textContent = "✓ Queued!"; btn.classList.add("queued"); }
      floatDismissTimer = setTimeout(removeFloatingBar, 2500);
    });

    floatBar.querySelector("#fetchr-float-close").addEventListener("click", removeFloatingBar);

    // Auto-dismiss after 12 s
    floatDismissTimer = setTimeout(removeFloatingBar, 12_000);
  }

  function removeFloatingBar() {
    floatBar?.remove();
    floatBar = null;
  }

  // ---------------------------------------------------------------- //
  // 2. Video element watcher (IDM-style auto-detect)                 //
  // ---------------------------------------------------------------- //

  const MEDIA_HOSTS = [
    "youtube.com", "youtu.be", "vimeo.com",
    "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "reddit.com", "v.redd.it",
    "twitch.tv", "dailymotion.com", "facebook.com",
    "soundcloud.com", "mixcloud.com",
    "erome.com",
    "pornhub.com", "xvideos.com", "xnxx.com",
    "xhamster.com", "spankbang.com", "thisvid.com",
    "redtube.com", "youporn.com", "tube8.com",
    "streamable.com", "medal.tv", "bilibili.com",
    "sjdbgame.com",
  ];

  // URL patterns that strongly indicate an advertisement video
  const AD_URL_PATTERNS = [
    /\/ads?\//i, /\/ad\//i, /\/advert/i, /\/preroll/i, /\/midroll/i,
    /doubleclick\.net/i, /googlesyndication/i, /googletagmanager/i,
    /adsystem/i, /adserver/i, /vast\.xml/i, /\.vast\./i,
    /imasdk\.googleapis/i, /pagead\//i, /adform\./i, /adnxs\.com/i,
    /rubiconproject/i, /openx\.net/i, /pubmatic/i, /appnexus/i,
    /springserve/i, /trafficjunky/i, /exoclick/i, /juicyads/i,
  ];

  // Attributes on <video> or its containers that signal ad players
  const AD_ATTR_PATTERNS = [
    /ima-ad/i, /vast/i, /preroll/i, /ad-container/i, /ad_container/i,
  ];

  function isKnownMediaHost(url) {
    try {
      const host = new URL(url).hostname.replace(/^www\./, "");
      return MEDIA_HOSTS.some((h) => host === h || host.endsWith("." + h));
    } catch { return false; }
  }

  /** Returns true if the video/URL looks like an advertisement. */
  function isAdVideo(video, src) {
    // 1. URL-based ad detection
    if (src && AD_URL_PATTERNS.some((p) => p.test(src))) return true;

    // 2. Attribute-based ad detection on the element and its ancestors
    const checkEl = (el) => {
      if (!el || el === document.body) return false;
      const attrs = Array.from(el.attributes || []);
      if (attrs.some((a) => AD_ATTR_PATTERNS.some((p) => p.test(a.name) || p.test(a.value)))) return true;
      const cls = el.className || "";
      const id  = el.id || "";
      if (AD_ATTR_PATTERNS.some((p) => p.test(cls) || p.test(id))) return true;
      return checkEl(el.parentElement);
    };
    if (checkEl(video)) return true;

    // 3. Size-based: skip if the rendered element is tiny (ad overlays are often small)
    const rect = video.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      if (rect.width < 200 || rect.height < 120) return true;
    }

    // 4. Duration-based: if duration is already known and very short (< 62s), likely an ad
    if (video.duration && isFinite(video.duration) && video.duration < 62) return true;

    return false;
  }

  /** Score a video element — higher = more likely to be the main content. */
  function videoScore(video) {
    const rect = video.getBoundingClientRect();
    const area = rect.width * rect.height;
    const nativeArea = (video.videoWidth || 0) * (video.videoHeight || 0);
    const durBonus = (video.duration && isFinite(video.duration) && video.duration > 60) ? 1e7 : 0;
    return area + nativeArea * 0.5 + durBonus;
  }

  /**
   * Scan page <script> tags for the first m3u8 or direct mp4 URL.
   * Sites like sjdbgame.com embed the stream URL in JavaScript config.
   */
  function findMediaUrlInPageScripts() {
    const scriptText = Array.from(document.querySelectorAll("script"))
      .map((s) => s.textContent).join("\n");

    // m3u8 first (HLS stream — most common for Asian video sites)
    const m3u8 = (scriptText.match(/['"`]?(https?:\/\/[^'"`<>\s]+\.m3u8[^'"`<>\s]*)/i) || [])[1];
    if (m3u8) return m3u8;

    // mp4 fallback (exclude known player CDNs like artplayer)
    const mp4  = (scriptText.match(/['"`]?(https?:\/\/[^'"`<>\s]+\.mp4[^'"`<>\s]*)/i) || [])[1];
    if (mp4 && !mp4.includes("artplayer") && !mp4.includes("demo")) return mp4;

    return null;
  }

  function buildMediaInfo(video) {
    const src    = video.currentSrc || video.src || "";
    const isBlob = src.startsWith("blob:");
    const HLS_DASH = /\.(m3u8|mpd)(\?|#|$)/i;

    if (isBlob) {
      // MSE/HLS stream — try to find the actual stream URL in page scripts first
      const streamUrl = findMediaUrlInPageScripts();
      if (streamUrl) {
        return { url: streamUrl, dlType: "media", title: document.title,
                 referer: window.location.href };
      }
      // No URL found in scripts — fall back to page URL via yt-dlp
      return { url: window.location.href, dlType: "media", title: document.title,
               referer: window.location.href };
    }

    if (isKnownMediaHost(window.location.href)) {
      if (src && src.startsWith("http")) {
        if (HLS_DASH.test(src)) {
          // HLS/DASH manifest — yt-dlp handles it better
          return { url: window.location.href, dlType: "media", title: document.title,
                   referer: window.location.href };
        }
        // Plain mp4/webm/etc on a known host — download directly with referer+cookies
        // This is faster than yt-dlp and works for sites yt-dlp doesn't know
        return { url: src, dlType: "direct", title: document.title,
                 referer: window.location.href };
      }
      // No usable src — fall back to page URL via yt-dlp
      return { url: window.location.href, dlType: "media", title: document.title,
               referer: window.location.href };
    }

    if (!src || !src.startsWith("http")) return null;

    // Ad check for non-media-host sites
    if (isAdVideo(video, src)) return null;

    const dlType = HLS_DASH.test(src) ? "media" : "direct";
    return { url: src, dlType, title: document.title, referer: window.location.href };
  }

  // Track the best video seen so far this page session
  let bestVideoScore = 0;

  function watchVideo(video) {
    if (video._fetchrWatching) return;
    video._fetchrWatching = true;

    const tryShow = () => {
      // Wait a tick for currentSrc + duration to settle
      setTimeout(() => {
        const src = video.currentSrc || video.src || "";

        // Quick ad rejection before building info
        if (isAdVideo(video, src)) return;

        // Only show if this video scores better than anything seen so far
        const score = videoScore(video);
        if (score < bestVideoScore * 0.8) return;  // don't replace a clearly better video
        bestVideoScore = Math.max(bestVideoScore, score);

        const info = buildMediaInfo(video);
        if (info) {
          // Remove old bar and show new one (better video found)
          seenUrls.delete(info.url.slice(0, 120));
          showFloatingBar(info);
        }
      }, 400);
    };

    video.addEventListener("play", tryShow);

    // Also check when duration becomes known (helps filter short ads that start before duration loads)
    video.addEventListener("durationchange", () => {
      if (!video.paused) tryShow();
    });

    // If already playing
    if (!video.paused && video.readyState >= 2) tryShow();
  }

  // Watch existing videos
  document.querySelectorAll("video").forEach(watchVideo);

  // Watch videos added later (YouTube injects its player dynamically)
  const videoObserver = new MutationObserver((mutations) => {
    for (const mut of mutations) {
      for (const node of mut.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === "VIDEO") watchVideo(node);
        node.querySelectorAll?.("video").forEach(watchVideo);
      }
    }
  });

  videoObserver.observe(document.documentElement, { childList: true, subtree: true });

  // ---------------------------------------------------------------- //
  // 3. Media DOM scanner                                              //
  // ---------------------------------------------------------------- //

  function scanPageForMedia() {
    const found = new Map();

    document.querySelectorAll("video, audio").forEach((el) => {
      const src = el.currentSrc || el.src;
      if (src && src.startsWith("http") && !found.has(src)) {
        found.set(src, { url: src, type: el.tagName.toLowerCase(), label: src.split("/").pop() });
      }
      el.querySelectorAll("source").forEach((s) => {
        if (s.src && !found.has(s.src)) {
          found.set(s.src, { url: s.src, type: "source", label: s.src.split("/").pop() });
        }
      });
    });

    const MEDIA_EXTS = /\.(mp4|mkv|webm|avi|mov|mp3|m4a|flac|wav|ogg|pdf|zip|rar|7z|tar|gz|iso|exe|dmg|pkg|deb|rpm|apk|ipa)(\?.*)?$/i;
    document.querySelectorAll("a[href]").forEach((a) => {
      const href = a.href;
      if (href && MEDIA_EXTS.test(href) && !found.has(href)) {
        found.set(href, { url: href, type: "link", label: a.textContent.trim() || href.split("/").pop() });
      }
    });

    document.querySelectorAll("img[src]").forEach((img) => {
      if (img.src?.startsWith("http") && !found.has(img.src)) {
        found.set(img.src, { url: img.src, type: "image", label: img.alt || img.src.split("/").pop() });
      }
    });

    const ogVideo = document.querySelector('meta[property="og:video"]');
    if (ogVideo?.content && !found.has(ogVideo.content)) {
      found.set(ogVideo.content, { url: ogVideo.content, type: "og:video", label: "og:video" });
    }

    return [...found.values()];
  }

  // ---------------------------------------------------------------- //
  // 4. Batch selector                                                 //
  // ---------------------------------------------------------------- //

  let batchActive  = false;
  let overlay      = null;
  let selectedItems = new Set();

  function startBatchSelect() {
    if (batchActive) return;
    batchActive = true;
    injectStyles();
    selectedItems.clear();
    removeFloatingBar();

    overlay = document.createElement("div");
    overlay.id = "fetchr-batch-overlay";

    const panel = document.createElement("div");
    panel.id = "fetchr-batch-panel";
    panel.innerHTML = `
      <span id="fetchr-batch-count">0 selected</span>
      <button id="fetchr-batch-dl">⬇ Download</button>
      <button id="fetchr-batch-cancel">✕ Cancel</button>
    `;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    document.querySelectorAll("a[href], img[src]").forEach((el) => {
      el.classList.add("fetchr-selectable");
      el._fetchrHandler = (e) => {
        e.preventDefault(); e.stopPropagation();
        const url = el.href || el.src;
        if (!url) return;
        if (selectedItems.has(url)) {
          selectedItems.delete(url); el.classList.remove("fetchr-selected");
        } else {
          selectedItems.add(url); el.classList.add("fetchr-selected");
        }
        document.getElementById("fetchr-batch-count").textContent = `${selectedItems.size} selected`;
      };
      el.addEventListener("click", el._fetchrHandler, true);
    });

    document.getElementById("fetchr-batch-dl").addEventListener("click", () => {
      const urls = [...selectedItems];
      if (urls.length === 0) return;
      chrome.runtime.sendMessage({ type: "batchDownload", urls });
      stopBatchSelect();
    });
    document.getElementById("fetchr-batch-cancel").addEventListener("click", stopBatchSelect);
  }

  function stopBatchSelect() {
    if (!batchActive) return;
    batchActive = false;
    document.querySelectorAll(".fetchr-selectable").forEach((el) => {
      el.classList.remove("fetchr-selectable", "fetchr-selected");
      if (el._fetchrHandler) {
        el.removeEventListener("click", el._fetchrHandler, true);
        delete el._fetchrHandler;
      }
    });
    overlay?.remove();
    overlay = null;
    selectedItems.clear();
  }

  // ---------------------------------------------------------------- //
  // 5. Message listener                                               //
  // ---------------------------------------------------------------- //

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "scanPage") {
      const items = scanPageForMedia();
      chrome.runtime.sendMessage({ type: "pageMediaFound", items });
      sendResponse({ count: items.length });
    }
    if (msg.type === "startBatchSelect") { startBatchSelect(); sendResponse({ ok: true }); }
    if (msg.type === "stopBatchSelect")  { stopBatchSelect();  sendResponse({ ok: true }); }
    if (msg.type === "showFloatingBar")  { showFloatingBar(msg.info); sendResponse({ ok: true }); }
  });

  // ---------------------------------------------------------------- //
  // Helpers                                                           //
  // ---------------------------------------------------------------- //

  function escHtml(str) {
    return String(str || "")
      .replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

})();
