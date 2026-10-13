// chttr meeting library. Talks only to the local chttr server that served it;
// the access token comes from the URL fragment (never sent in Referer headers).
"use strict";

const TOKEN = location.hash.slice(1);
const $ = (sel) => document.querySelector(sel);
const state = { meetings: [], current: null, view: "meetings", styles: [] };

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { "X-Chttr-Token": TOKEN, "Content-Type": "application/json" } });
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

function setView(view, render = true) {
  state.view = view;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
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
  try {
    [state.meetings, state.styles] = await Promise.all([api("/api/meetings"), api("/api/styles")]);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderMeetingList();
  if (state.meetings.length) openMeeting(state.meetings[0].id);
}

start();
