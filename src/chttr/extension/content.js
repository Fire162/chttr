// chttr: runs on meet.google.com. Reports to the chttr daemon (via background.js):
//   joined / left      when you enter or leave a call (with the Meet code)
//   events             {type: "speaking", name, text, t}: the captions name who is talking
//                      {type: "mute", muted, t}: your Meet mic button
//   heartbeat          every 3 s during a call
// Only while chttr is recording this call: captions are switched on if they're
// off (and hidden again, unless you had them on yourself), and a small "chttr is
// recording" badge stays on the page. When the recording stops, captions we
// turned on are turned off. Nothing is stored or sent anywhere except the local
// chttr daemon.
//
// Meet's DOM is undocumented and changes. Everything here prefers attributes that
// have stayed stable (jsname, aria roles, the Material icon names inside buttons)
// and falls back to structure. If names stop appearing in transcripts after a
// Meet update, the caption selectors below are the place to look.

(() => {
  const POLL_MS = 300;
  const FLUSH_MS = 1000;
  const HEARTBEAT_MS = 3000;     // also how quickly the page learns that chttr started recording
  const HIDE_CAPTIONS_WE_ENABLED = true;
  const CODE_RE = /^\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/;

  let inCall = false;
  let code = null;
  let lastHeartbeat = 0;
  let lastFlush = 0;
  let queue = [];
  let lastText = new Map();     // speaker -> caption text last seen
  let muted = null;
  let captionsTries = 0;
  let weEnabledCaptions = false;
  let nextCaptionsTry = 0;
  let recording = false;        // what the daemon last said about this call

  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.type !== "chttr-state") return;
    const was = recording;
    recording = !!msg.recording;
    if (recording && !was) {
      captionsTries = 0;
      nextCaptionsTry = Date.now() + 1000;
    }
    if (!recording && was) stopUsingCaptions();
    showBadge(recording);
  });

  // ---- recording badge ---------------------------------------------------
  function showBadge(on) {
    let badge = document.getElementById("chttr-recording-badge");
    if (!on) {
      if (badge) badge.remove();
      return;
    }
    if (badge) return;
    badge = document.createElement("div");
    badge.id = "chttr-recording-badge";
    badge.textContent = "● chttr is recording";
    badge.title = "chttr is saving a transcript of this call on your computer";
    Object.assign(badge.style, {
      position: "fixed", top: "8px", left: "50%", transform: "translateX(-50%)", zIndex: "2147483647",
      padding: "3px 10px", borderRadius: "999px", font: "500 12px/1.4 system-ui, sans-serif",
      color: "#fff", background: "rgba(198, 40, 40, 0.85)", pointerEvents: "none",
    });
    document.documentElement.appendChild(badge);
  }

  function send(msg) {
    try {
      chrome.runtime.sendMessage(msg);
    } catch (e) {
      // extension reloaded or updated: this page's copy is orphaned; stop quietly
      clearInterval(timer);
    }
  }

  function meetingCode() {
    const m = location.pathname.match(CODE_RE);
    return m ? m[1] : null;
  }

  function label(el) {
    return ((el.getAttribute("aria-label") || "") + " " + (el.getAttribute("data-tooltip") || "")).trim();
  }

  function iconButton(icons) {
    // Meet's buttons render Material Symbols by ligature: <i>call_end</i>
    for (const i of document.querySelectorAll("button i, button span, [role='button'] i")) {
      if (icons.includes(i.textContent.trim())) return i.closest("button, [role='button']");
    }
    return null;
  }

  function leaveButton() {
    for (const b of document.querySelectorAll("button[aria-label], [role='button'][aria-label]")) {
      if (/leave call|leave meeting|hang up|end call/i.test(b.getAttribute("aria-label"))) return b;
    }
    return document.querySelector('[jsname="CQylAd"]') || iconButton(["call_end"]);
  }

  function meetingTitle() {
    const el = document.querySelector("[data-meeting-title]");
    if (el) return el.getAttribute("data-meeting-title") || el.textContent.trim();
    const t = document.title.replace(/^Meet\s*[-–—:]\s*/, "").trim();
    return t === code ? "" : t;
  }

  // ---- mic --------------------------------------------------------------
  function micMuted() {
    for (const b of document.querySelectorAll("button[data-is-muted], [role='button'][data-is-muted]")) {
      if (/microphone|mic\b/i.test(label(b))) return b.getAttribute("data-is-muted") === "true";
    }
    for (const b of document.querySelectorAll("button[aria-label], [role='button'][aria-label]")) {
      const l = b.getAttribute("aria-label");
      if (/turn on microphone|unmute/i.test(l)) return true;
      if (/turn off microphone|^mute\b/i.test(l)) return false;
    }
    if (iconButton(["mic_off"])) return true;
    if (iconButton(["mic", "mic_none"])) return false;
    return null;
  }

  // ---- captions ---------------------------------------------------------
  const CAPTION_WORDS = ["caption", "subtitle", "untertitel", "sous-titre", "subtítulo", "legenda", "sottotitoli", "ondertitel"];

  function isDialog(el) {
    return !!el.closest('[role="dialog"], [role="alertdialog"]');
  }

  function captionContainer() {
    for (const sel of ['[jsname="tgaKEf"]', '[jsname="DS9Ooe"]']) {
      const el = document.querySelector(sel);
      if (el && !isDialog(el)) return el;
    }
    for (const el of document.querySelectorAll('[role="region"][aria-label]')) {
      const l = el.getAttribute("aria-label").toLowerCase();
      if (CAPTION_WORDS.some((w) => l.includes(w)) && !isDialog(el)) return el;
    }
    return null;
  }

  // speaker -> current caption text, from Meet's caption blocks
  function readCaptions(container) {
    const out = new Map();
    const blocks = container.querySelectorAll('[jsname="YSxPC"]');
    if (blocks.length) {
      for (const b of blocks) {
        const name = b.querySelector('[jsname="r4nke"]')?.textContent.trim();
        const text = b.querySelector('[jsname="bVV8Bd"]')?.textContent.trim();
        if (name && text) out.set(name, text);
      }
      if (out.size) return out;
    }
    // Structural fallback: each child block is [avatar] name text; the first
    // text-bearing child is the name, the rest is what they said.
    for (const block of container.children) {
      const parts = [...block.children].map((el) => el.textContent.trim()).filter(Boolean);
      if (parts.length < 2 || parts[0].length > 60) continue;
      out.set(parts[0], parts.slice(1).join(" "));
    }
    return out;
  }

  function captionsButton() {
    const candidates = [...document.querySelectorAll("button, [role='button']")].filter((b) => {
      const l = label(b).toLowerCase();
      return CAPTION_WORDS.some((w) => l.includes(w)) && !/setting|option|language|sprache|langue/.test(l);
    });
    // The toggle's label carries its keyboard shortcut "(c)"; settings entries don't.
    return candidates.find((b) => /\(c\)/i.test(label(b))) || iconButton(["closed_caption", "closed_caption_off"]) || candidates[0] || null;
  }

  function captionsOn(btn) {
    const pressed = btn.getAttribute("aria-pressed");
    if (pressed === "true") return true;
    if (pressed === "false") return false;
    return /turn off|hide|disable/i.test(label(btn));
  }

  function stopUsingCaptions() {
    const container = captionContainer();
    if (container) {
      const region = container.closest('[role="region"]') || container;
      if (region.dataset.chttrHidden) {
        delete region.dataset.chttrHidden;
        region.style.removeProperty("opacity");
        region.style.removeProperty("pointer-events");
      }
    }
    if (weEnabledCaptions) {
      const btn = captionsButton();
      if (btn && captionsOn(btn)) btn.click();
      weEnabledCaptions = false;
    }
  }

  function ensureCaptions() {
    if (!recording || captionContainer() || captionsTries >= 6 || Date.now() < nextCaptionsTry) return;
    captionsTries += 1;
    nextCaptionsTry = Date.now() + 5000;
    const btn = captionsButton();
    if (!btn || captionsOn(btn)) return;
    btn.click();
    weEnabledCaptions = true;
  }

  function hideCaptions(container) {
    if (!HIDE_CAPTIONS_WE_ENABLED || !weEnabledCaptions) return;
    const region = container.closest('[role="region"]') || container;
    if (region.dataset.chttrHidden) return;
    region.dataset.chttrHidden = "1";
    region.style.setProperty("opacity", "0", "important");
    region.style.setProperty("pointer-events", "none", "important");
  }

  // ---- loop -------------------------------------------------------------
  function tick() {
    const now = Date.now();
    const c = meetingCode();
    const nowInCall = !!(c && leaveButton());

    if (nowInCall && (!inCall || c !== code)) {
      if (inCall && code) send({ type: "left", code, t: now });
      inCall = true;
      code = c;
      lastText = new Map();
      muted = null;
      captionsTries = 0;
      nextCaptionsTry = now + 3000;    // let the call UI settle first
      send({ type: "joined", code, title: meetingTitle(), url: location.href, t: now });
    } else if (!nowInCall && inCall) {
      flush(now);
      send({ type: "left", code, t: now });
      inCall = false;
      code = null;
      recording = false;
      showBadge(false);
      return;
    }
    if (!inCall) return;

    ensureCaptions();
    const container = recording ? captionContainer() : null;
    if (container) {
      hideCaptions(container);
      const current = readCaptions(container);
      for (const [name, text] of current) {
        if (lastText.get(name) !== text) queue.push({ type: "speaking", name, text: text.slice(-200), t: now });
      }
      lastText = current;
    }

    const m = micMuted();
    if (m !== null && m !== muted) {
      muted = m;
      queue.push({ type: "mute", muted: m, t: now });
    }

    if (now - lastFlush >= FLUSH_MS) flush(now);
    if (now - lastHeartbeat >= HEARTBEAT_MS) {
      lastHeartbeat = now;
      send({ type: "heartbeat", code, title: meetingTitle(), t: now });
    }
  }

  function flush(now) {
    lastFlush = now;
    if (!queue.length || !code) return;
    send({ type: "events", code, events: queue });
    queue = [];
  }

  window.addEventListener("pagehide", () => {
    if (inCall && code) {
      flush(Date.now());
      send({ type: "left", code, t: Date.now() });
    }
  });

  const timer = setInterval(tick, POLL_MS);
})();
