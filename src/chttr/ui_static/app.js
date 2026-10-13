// chttr meeting library and Lingo. Talks only to the local chttr server that served it;
// the access token comes from the URL fragment (never sent in Referer headers),
// optionally followed by the view to open: #TOKEN/lingo.
"use strict";

const [TOKEN, START_VIEW] = location.hash.slice(1).split("/");
const $ = (sel) => document.querySelector(sel);
const state = { meetings: [], current: null, view: "meetings", styles: [] };

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, { ...options, headers: { "X-Chttr-Token": TOKEN, "Content-Type": "application/json" } });
  } catch {
    throw new Error("Lost the connection to chttr. Open this page again from the chttr menu.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

// "**word**" markers from the search snippets -> <mark>, without ever using innerHTML
function highlighted(text) {
  return String(text).split("**").map((part, i) => (i % 2 ? el("mark", {}, part) : part));
}

const ts = (s) => {
  s = Math.floor(s);
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((n) => String(n).padStart(2, "0")).join(":");
};
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

function toast(message, error = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), error ? 6000 : 2500);
}

// ---- list ---------------------------------------------------------------------------
function renderMeetingList() {
  const list = $("#list");
  list.replaceChildren(
    el("div", { class: "section-label" }, `${state.meetings.length} meetings`),
    ...state.meetings.map((m) =>
      el("button", { class: "item" + (state.current && state.current.id === m.id ? " active" : ""), onclick: () => openMeeting(m.id) },
        el("div", { class: "title" }, m.title),
        el("div", { class: "meta" }, `${when(m.when)} · ${m.minutes} min · ${m.speakers.slice(0, 4).join(", ")}`),
        m.summary ? el("div", { class: "snip meta" }, m.summary.slice(0, 140)) : null)));
}

async function runSearch(q) {
  if (!q.trim()) {
    renderMeetingList();
    return;
  }
  const hits = await api(`/api/search?q=${encodeURIComponent(q)}`);
  $("#list").replaceChildren(
    el("div", { class: "section-label" }, hits.length ? `${hits.length} matches` : "No matches"),
    ...hits.map((h) =>
      el("button", { class: "item", onclick: () => openMeeting(h.meeting_id, h.kind === "line" ? h.start : null) },
        el("div", { class: "title" }, h.title),
        el("div", { class: "meta" }, `${when(h.when)}${h.kind === "line" ? ` · ${ts(h.start)} · ${h.speaker}` : " · notes"}`),
        el("div", { class: "snip" }, ...highlighted(h.snippet)))));
}

// ---- meeting -----------------------------------------------------------------------------
async function openMeeting(id, at = null) {
  setView("meetings", false);
  const m = await api(`/api/meeting/${encodeURIComponent(id)}`);
  state.current = m;
  renderMeeting(m, at);
  if (!$("#q").value.trim()) renderMeetingList();
}

function renderNotes(notes) {
  const out = [];
  for (const sec of notes.sections || []) {
    const items = (sec.items || []).filter((x) => String(x).trim());
    if (!items.length) continue;
    out.push(el("h2", {}, sec.title));
    if (sec.kind === "paragraph") out.push(...items.map((x) => el("p", {}, x)));
    else if (sec.kind === "quotes") out.push(...items.map((x) => el("blockquote", {}, x)));
    else out.push(el("ul", {}, items.map((x) => el("li", {}, x))));
  }
  const actions = notes.action_items || [];
  if (actions.length) {
    out.push(el("h2", {}, notes.actions_heading || "Action items"));
    out.push(el("ul", { class: "actions" }, actions.map((a) =>
      el("li", {}, a.owner ? el("span", { class: "owner" }, `${a.owner}: `) : null, a.task, a.due ? el("span", { class: "due" }, ` (due ${a.due})`) : null))));
  }
  if (notes.error) out.push(el("p", { class: "meta" }, `Notes could not be written: ${notes.error}`));
  return out;
}

function renderMeeting(m, at) {
  const audio = m.has_audio ? el("audio", { controls: true, preload: "metadata", src: `/api/meeting/${encodeURIComponent(m.id)}/audio?token=${encodeURIComponent(TOKEN)}` }) : null;
  const styleSelect = el("select", { "aria-label": "Notes style", onchange: (e) => restyle(m.id, e.target.value) },
    state.styles.map((s) => el("option", { value: s.id, selected: s.id === (m.notes.style || "") ? "selected" : null }, s.name)));
  const speakers = Object.entries(m.speakers).map(([label, sp]) =>
    el("button", { class: "chip" + (sp.source === "unknown" ? " unknown" : ""), title: `${sp.source}${sp.email ? " · " + sp.email : ""} · click to rename`,
      onclick: () => renameSpeaker(m.id, label) }, `${label} · ${sp.minutes} min`));
  const lines = m.transcript.map((u) => {
    const hit = at !== null && Math.abs(u.start - at) < 0.5;
    const seek = () => { if (audio) { audio.currentTime = u.start; audio.play(); } };
    return [el("button", { class: "t", onclick: seek, title: audio ? "Play from here" : "" }, ts(u.start)),
            el("div", { class: "who" }, u.speaker),
            el("div", { class: "line" + (hit ? " hit" : ""), id: hit ? "hit" : null }, u.text)];
  });
  $("#detail").replaceChildren(...[
    el("h1", {}, m.title),
    el("div", { class: "sub" }, el("span", {}, `${when(m.when)} · ${m.minutes} min`), state.styles.length ? styleSelect : null),
    el("div", { class: "chips" }, speakers),
    audio,
    el("div", { class: "notes" }, renderNotes(m.notes || {})),
    el("h2", {}, "Transcript"),
    el("div", { class: "transcript" }, lines.flat()),
  ].filter(Boolean));
  const target = document.getElementById("hit");
  if (target) {
    target.scrollIntoView({ block: "center" });
    if (audio) audio.currentTime = at;
  } else $("#detail").scrollTop = 0;
}

async function renameSpeaker(id, label) {
  const name = prompt(`Who is "${label}"?`, label.startsWith("Speaker") || label.startsWith("In the room") ? "" : label);
  if (!name || name.trim() === label) return;
  try {
    const m = await api(`/api/meeting/${encodeURIComponent(id)}/rename`, { method: "POST", body: JSON.stringify({ old: label, new: name.trim() }) });
    state.current = m;
    renderMeeting(m, null);
    toast(`Renamed to ${name.trim()}; chttr will recognise that voice next time.`);
  } catch (e) { toast(e.message, true); }
}

async function restyle(id, style) {
  toast("Rewriting the notes…");
  try {
    const m = await api(`/api/meeting/${encodeURIComponent(id)}/style`, { method: "POST", body: JSON.stringify({ style }) });
    state.current = m;
    renderMeeting(m, null);
    toast("Notes rewritten.");
  } catch (e) { toast(e.message, true); }
}

// ---- action items ---------------------------------------------------------------------------
async function renderActions(owner = "") {
  const items = await api(`/api/actions?owner=${encodeURIComponent(owner)}`);
  const filter = el("input", { class: "filter", type: "search", placeholder: "Filter by owner…", value: owner,
    oninput: (e) => { clearTimeout(renderActions.t); renderActions.t = setTimeout(() => renderActions(e.target.value), 250); } });
  $("#detail").replaceChildren(
    el("h1", {}, "Action items"), filter,
    items.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Owner"), el("th", {}, "Task"), el("th", {}, "Due"), el("th", {}, "Meeting"))),
      el("tbody", {}, items.map((a) => el("tr", {}, el("td", {}, a.owner || "–"), el("td", {}, a.task), el("td", {}, a.due || ""),
        el("td", {}, el("a", { onclick: () => openMeeting(a.meeting_id) }, `${a.meeting} · ${when(a.when)}`))))))
      : el("p", { class: "empty" }, "No action items yet."));
  if (owner) filter.focus();
}

// ---- Lingo: how chttr spells your words -----------------------------------------------------
// Pick a past dictation, click the words that came out wrong (neighbouring words extend the
// selection; shift-click selects a range), type the right spelling. The daemon reads the
// dictionary on every dictation, so a fix applies to the next one.
const lingo = { words: [], recent: [], pick: null, from: -1, to: -1, draft: "" };

const tokens = (s) => s.split(/\s+/).filter(Boolean);
const trimPunctuation = (s) => s.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");

async function lingoDo(action, body, message) {
  try {
    Object.assign(lingo, await api(`/api/lingo/${action}`, { method: "POST", body: JSON.stringify(body) }));
    if (message) toast(message);
    renderLingo();
  } catch (e) { toast(e.message, true); }
}

function renderLingoList() {
  $("#list").replaceChildren(
    el("div", { class: "section-label" }, lingo.recent.length ? "Recent dictations" : "No dictations yet"),
    ...lingo.recent.map((d, i) =>
      el("button", { class: "item" + (lingo.pick === i ? " active" : ""), onclick: () => pickDictation(i) },
        el("div", { class: "snip" }, d.heard.length > 140 ? d.heard.slice(0, 139) + "…" : d.heard),
        el("div", { class: "meta" }, [when(d.ts), d.app].filter(Boolean).join(" · ")))));
}

function pickDictation(i) {
  Object.assign(lingo, { pick: i, from: -1, to: -1, draft: "" });
  renderLingo();
}

function pickWord(i, extend) {
  const { from, to } = lingo;
  if (from < 0) lingo.from = lingo.to = i;
  else if (extend) [lingo.from, lingo.to] = [Math.min(from, i), Math.max(to, i)];
  else if (i === from - 1) lingo.from = i;
  else if (i === to + 1) lingo.to = i;
  else if (i === from && i === to) lingo.from = lingo.to = -1;
  else if (i === from) lingo.from = i + 1;
  else if (i === to) lingo.to = i - 1;
  else lingo.from = lingo.to = i;
  lingo.draft = selectedPhrase();
  renderLingo();
  const input = document.getElementById("lingo-right");
  if (input) { input.focus(); input.select(); }
}

function selectedPhrase() {
  if (lingo.pick === null || lingo.from < 0) return "";
  return trimPunctuation(tokens(lingo.recent[lingo.pick].heard).slice(lingo.from, lingo.to + 1).join(" "));
}

function fixPanel() {
  if (lingo.pick === null) {
    return el("p", { class: "meta" }, lingo.recent.length ? "Pick a dictation on the left." : "Dictate something first; it will show up here.");
  }
  const d = lingo.recent[lingo.pick];
  const words = tokens(d.heard).map((w, i) => {
    const on = lingo.from >= 0 && i >= lingo.from && i <= lingo.to;
    return el("button", { class: "word" + (on ? " on" : ""), "aria-pressed": on ? "true" : "false",
      onclick: (e) => pickWord(i, e.shiftKey) }, w);
  });
  const heard = selectedPhrase();
  const now = el("div", { class: "meta now" });
  api("/api/lingo/try", { method: "POST", body: JSON.stringify({ text: d.heard }) })
    .then((r) => { now.textContent = r.typed !== d.heard ? `With your words, chttr now types: “${r.typed}”` : ""; })
    .catch(() => {});
  const form = heard ? el("form", { class: "lingo-form", onsubmit: (e) => {
      e.preventDefault();
      const word = $("#lingo-right").value.trim();
      if (!word) return;
      if (word === heard) { toast("That's how chttr already spells it."); return; }
      Object.assign(lingo, { from: -1, to: -1, draft: "" });
      lingoDo("learn", { word, heard: [heard] }, `From now on, “${heard}” is typed as “${word}”.`);
    } },
    el("span", {}, "“", el("strong", {}, heard), "” should be"),
    el("input", { id: "lingo-right", value: lingo.draft, "aria-label": "Correct spelling", oninput: (e) => (lingo.draft = e.target.value) }),
    el("button", { class: "primary", type: "submit" }, "Always spell it this way"))
    : el("p", { class: "meta" }, "Click the word, or words, that came out wrong.");
  return [el("div", { class: "words" }, words), form, now];
}

function addPanel() {
  const heard = el("input", { placeholder: "what chttr hears (optional)", "aria-label": "What chttr hears" });
  const word = el("input", { placeholder: "how to spell it", "aria-label": "Correct spelling" });
  return el("form", { class: "lingo-form", onsubmit: (e) => {
      e.preventDefault();
      if (!word.value.trim()) { word.focus(); return; }
      const h = heard.value.trim();
      lingoDo("learn", { word: word.value.trim(), heard: h ? [h] : [] },
        h ? `From now on, “${h}” is typed as “${word.value.trim()}”.` : `Added “${word.value.trim()}”.`);
    } },
    el("span", {}, "When I say"), heard, el("span", {}, "type"), word, el("button", { class: "primary", type: "submit" }, "Add"));
}

function wordsTable() {
  if (!lingo.words.length) return el("p", { class: "meta" }, "No words yet. Fix one from a dictation above, or add one.");
  const rename = (w) => {
    const name = prompt(`How should “${w}” be spelled?`, w);
    if (name && name.trim() && name.trim() !== w) lingoDo("rename", { old: w, new: name.trim() }, `Now spelled “${name.trim()}”.`);
  };
  const addHeard = (w) => {
    const h = prompt(`What does chttr type instead of “${w}”?`);
    if (h && h.trim()) lingoDo("learn", { word: w, heard: [h.trim()] }, `“${h.trim()}” is typed as “${w}” from now on.`);
  };
  const forget = (w, n) => {
    if (confirm(`Remove “${w}”${n ? ` and the ${n} way${n > 1 ? "s" : ""} it was misheard` : ""}?`)) lingoDo("forget", { word: w }, `Removed “${w}”.`);
  };
  return el("table", { class: "lingo-words" },
    el("thead", {}, el("tr", {}, el("th", {}, "Spelled"), el("th", {}, "When chttr hears"), el("th", {}, ""))),
    el("tbody", {}, lingo.words.map((w) => el("tr", {},
      el("td", {}, el("button", { class: "spelling", title: "Change the spelling", onclick: () => rename(w.word) }, w.word)),
      el("td", {}, el("div", { class: "chips" },
        w.heard.map((h) => el("span", { class: "chip heard" }, h,
          el("button", { class: "x", title: `Stop changing “${h}”`, "aria-label": `Remove “${h}”`, onclick: () => lingoDo("unhear", { heard: h }) }, "×"))),
        w.vocabulary && /^\p{L}{3,}$/u.test(w.word) ? el("span", { class: "chip note", title: "Single words that sound like this are corrected too" }, "≈ sound-alikes") : null,
        el("button", { class: "chip add", onclick: () => addHeard(w.word) }, "+ add"))),
      el("td", { class: "right" }, el("button", { class: "link danger", onclick: () => forget(w.word, w.heard.length) }, "Remove"))))));
}

function tryPanel() {
  const out = el("div", { class: "try-out", "aria-live": "polite" });
  const input = el("input", { class: "filter wide", placeholder: "Type what chttr heard, e.g. my name is so rob", "aria-label": "Try a sentence",
    oninput: (e) => {
      clearTimeout(tryPanel.t);
      const text = e.target.value;
      tryPanel.t = setTimeout(() => {
        if (!text.trim()) { out.textContent = ""; return; }
        api("/api/lingo/try", { method: "POST", body: JSON.stringify({ text }) })
          .then((r) => { out.textContent = `→ ${r.typed}`; }).catch((x) => toast(x.message, true));
      }, 200);
    } });
  return [input, out];
}

function renderLingo() {
  renderLingoList();
  $("#detail").replaceChildren(
    el("h1", {}, "Lingo"),
    el("div", { class: "sub" }, "Teach chttr how you spell things. Fixes apply from your next dictation, everywhere."),
    el("h2", {}, "Fix a dictation"), ...[fixPanel()].flat(),
    el("h2", {}, "Add a spelling"), addPanel(),
    el("h2", {}, "Your words"), wordsTable(),
    el("p", { class: "meta" }, "Single words that sound like one of your words (Sorov, Suhrab → Sohrab) are corrected automatically. Phrases need each way chttr hears them."),
    el("h2", {}, "Try it"), ...tryPanel());
}

async function openLingo() {
  try {
    Object.assign(lingo, await api("/api/lingo"));
  } catch (e) { toast(e.message, true); return; }
  if (lingo.pick === null && lingo.recent.length) lingo.pick = 0;
  if (lingo.pick !== null && lingo.pick >= lingo.recent.length) lingo.pick = lingo.recent.length ? 0 : null;
  renderLingo();
}

function setView(view, render = true) {
  const leaving = state.view === "lingo" && view !== "lingo";
  state.view = view;
  document.title = view === "lingo" ? "chttr · Lingo" : "chttr · meetings";
  $("#search").hidden = view === "lingo";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  if (leaving) renderMeetingList();
  if (render && view === "lingo") openLingo();
  if (render && view === "actions") renderActions();
  if (render && view === "meetings") {
    if (state.current) renderMeeting(state.current, null);
    else $("#detail").replaceChildren(el("div", { class: "empty" }, "Pick a meeting, or search across all of them."));
  }
}

// ---- start ---------------------------------------------------------------------------------------
async function start() {
  if (!TOKEN) {
    $("#detail").replaceChildren(el("div", { class: "empty" }, "Open this page with the link `chttr ui` prints."));
    return;
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setView(t.dataset.view)));
  let timer;
  $("#q").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => runSearch(e.target.value).catch((x) => toast(x.message, true)), 200); });
  $("#search").addEventListener("submit", (e) => e.preventDefault());
  setInterval(() => api("/api/ping").catch(() => {}), 60000);   // keeps a `chttr lingo` server alive while open
  if (START_VIEW === "lingo") setView("lingo");
  try {
    [state.meetings, state.styles] = await Promise.all([api("/api/meetings"), api("/api/styles")]);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  if (state.view === "lingo") return;
  renderMeetingList();
  if (state.meetings.length) openMeeting(state.meetings[0].id);
}

start();
