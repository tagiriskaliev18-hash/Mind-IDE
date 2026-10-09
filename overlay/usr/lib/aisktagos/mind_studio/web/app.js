// Mind Studio — интерфейс. Говорит с локальным сервером (server.py) по JSON и SSE.
"use strict";

const TOKEN = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("token") || "";
sessionStorage.setItem("token", TOKEN);
history.replaceState(null, "", location.pathname);

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => MD.esc(String(s ?? ""));

const ICONS = {
  mind: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>',
  claude: '<svg viewBox="0 0 24 24"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"/></svg>',
  antigravity: '<svg viewBox="0 0 24 24"><path d="M12 3 4 20h4l4-9 4 9h4z"/></svg>',
  all: '<svg viewBox="0 0 24 24"><circle cx="8" cy="9" r="3"/><circle cx="16" cy="9" r="3"/><circle cx="12" cy="16" r="3"/></svg>',
  chat: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  caret: '<svg class="caret" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>',
  tool: '<svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/></svg>',
};

const S = {
  state: null, chats: [], chat: null, agent: "mind", mode: "auto", skills: [], attachments: [],
  busy: false, view: "chat", theme: localStorage.getItem("theme") || "light",
};

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const r = await fetch("/api/" + path, {
    method: opts.method || "GET",
    headers: { "X-Studio-Token": TOKEN, "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(text, ms = 2600) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), ms);
}

function ago(ts) {
  const d = (Date.now() / 1000 - ts);
  if (d < 60) return "только что";
  if (d < 3600) return Math.floor(d / 60) + " мин";
  if (d < 86400) return Math.floor(d / 3600) + " ч";
  return Math.floor(d / 86400) + " дн";
}

// ---------------------------------------------------------------- тема
function applyTheme() {
  document.documentElement.dataset.theme = S.theme;
  localStorage.setItem("theme", S.theme);
}

// ---------------------------------------------------------------- боковая панель
async function loadSide() {
  S.chats = await api("chats");
  const byProject = {};
  for (const c of S.chats) (byProject[c.project || ""] ||= []).push(c);
  const projects = S.state.projects || [];
  $("#projects").innerHTML = projects.length ? projects.map((p) => `
    <div class="proj">
      <button class="proj-head" data-project="${esc(p.name)}" data-path="${esc(p.path)}">
        ${ICONS.folder}<span>${esc(p.name)}</span><span class="add" title="Новый разговор в проекте">${ICONS.plus}</span>
      </button>
      ${(byProject[p.name] || []).slice(0, 6).map((c) => chatItem(c)).join("")}
    </div>`).join("") : `<div class="empty-side">Добавьте папки проектов в настройках</div>`;
  const known = new Set(projects.map((p) => p.name));
  const loose = S.chats.filter((c) => !known.has(c.project || "")).slice(0, 12);
  $("#recent").innerHTML = loose.length ? loose.map((c) => chatItem(c, true)).join("") :
    `<div class="empty-side">Здесь появятся разговоры</div>`;
  $$(".proj-head").forEach((b) => b.onclick = () => newChat(b.dataset.project, b.dataset.path));
  bindChatItems();
}

function chatItem(c, flat = false) {
  const active = S.chat && S.chat.id === c.id ? " active" : "";
  return `<button class="item${flat ? " flat" : ""}${active}" data-chat="${c.id}" title="${esc(c.title)}">
    <span class="t">${esc(c.title)}</span><span class="x" data-del="${c.id}" title="Удалить">${ICONS.x}</span></button>`;
}

function bindChatItems() {
  $$("[data-chat]").forEach((b) => b.onclick = (e) => {
    const del = e.target.closest("[data-del]");
    if (del) { e.stopPropagation(); return deleteChat(del.dataset.del); }
    openChat(b.dataset.chat);
  });
}

async function loadAgy() {
  const st = S.state.status.antigravity;
  $("#agy-dot").className = "dot" + (st.ready ? " on" : "");
  $("#agy-dot").title = st.detail;
  if (!st.installed) { $("#agy-section").classList.add("hidden"); return; }
  const items = await api("antigravity/history").catch(() => []);
  $("#agy-history").innerHTML = items.slice(0, 8).map((c) => `
    <button class="item flat" data-agy="${c.id}" title="${esc(c.workspace)} · ${esc(c.updated)}">
      <span class="t">${esc(c.title)}</span></button>`).join("") || `<div class="empty-side">Нет разговоров</div>`;
  $$("[data-agy]").forEach((b) => b.onclick = () => openAgy(b.dataset.agy, b.textContent.trim()));
}

function renderHealth() {
  const st = S.state.status;
  $("#health").innerHTML = ["mind", "claude", "antigravity"].map((k) =>
    `<span class="a"><span class="dot${st[k].ready ? " on" : ""}"></span>${S.state.agents.find((a) => a.id === k).title}</span>`).join("");
  $("#health").title = ["mind", "claude", "antigravity"].map((k) => `${k}: ${st[k].detail}`).join("\n");
}

// ---------------------------------------------------------------- навигация
function show(view) {
  S.view = view;
  $("#view-chat").classList.toggle("hidden", view !== "chat");
  $("#view-panel").classList.toggle("hidden", view === "chat");
  $$(".nav-item[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
}

function crumbs(parts) {
  $("#crumbs").innerHTML = parts.map((p, i) =>
    `<span class="${i === parts.length - 1 ? "cur" : ""}">${esc(p)}</span>`).join('<span class="sep">/</span>');
}

// ---------------------------------------------------------------- чат
async function newChat(project = "", path = "") {
  S.chat = { id: null, title: "Новый разговор", project, project_path: path, messages: [] };
  show("chat");
  renderChat();
  $("#input").focus();
}

async function openChat(id) {
  S.chat = await api("chats/" + id);
  if (S.chat.agent && S.chat.agent !== "all") S.agent = S.chat.agent;
  show("chat");
  renderChat();
  loadSide();
}

async function deleteChat(id) {
  await api("chats/" + id, { method: "DELETE" });
  if (S.chat && S.chat.id === id) newChat();
  loadSide();
  toast("Разговор удалён");
}

async function openAgy(id, title) {
  const msgs = await api("antigravity/conversation/" + id);
  S.chat = { id: null, readonly: true, title, project: "Antigravity", messages: msgs };
  show("chat");
  renderChat();
}

function agentMeta(id) { return S.state.agents.find((a) => a.id === id) || { title: id }; }

function renderChat() {
  const c = S.chat;
  crumbs([c.project || "Mind Studio", c.title]);
  const feed = $("#feed");
  if (!c.messages.length) { feed.innerHTML = hero(); bindHero(); }
  else feed.innerHTML = c.messages.map(renderMsg).join("");
  bindCode(feed);
  $("#composer").classList.toggle("hidden", !!c.readonly);
  $("#foot-note").textContent = c.readonly ? "Разговор Antigravity — только просмотр. Новый вопрос задайте в новом разговоре." : "";
  scrollDown(true);
}

function hero() {
  const st = S.state.status;
  const cards = S.state.agents.map((a) => {
    const ready = a.id === "all" ? true : st[a.id].ready;
    return `<button class="agent-card${S.agent === a.id ? " sel" : ""}${ready ? "" : " off"}" data-agent="${a.id}">
      <div class="ic ${a.id}">${ICONS[a.id]}</div>
      <div class="n">${esc(a.title)}</div><div class="s">${esc(ready ? a.subtitle : st[a.id].detail)}</div></button>`;
  }).join("");
  const sug = [
    ["Разобрать ошибку", "Объясни эту ошибку и как её исправить:\n"],
    ["Спроектировать фичу", "Спроектируй архитектуру и план реализации для: "],
    ["Сделать красивый интерфейс", "Сверстай стильный интерфейс в духе Apple для: "],
    ["Ревью кода", "Сделай строгое ревью этого кода:\n"],
  ].map(([b, t]) => `<button data-prefill="${esc(t)}"><b>${esc(b)}</b>${esc(t.split(":")[0])}</button>`).join("");
  return `<div class="hero"><h1>Чем займёмся?</h1>
    <p>Один разговор, все ваши ИИ. Выберите, кто отвечает, или оставьте «Все сразу».</p>
    <div class="agent-cards">${cards}</div><div class="suggest">${sug}</div></div>`;
}

function bindHero() {
  $$("[data-agent]").forEach((b) => b.onclick = () => { S.agent = b.dataset.agent; renderPickers(); renderChat(); });
  $$("[data-prefill]").forEach((b) => b.onclick = () => { $("#input").value = b.dataset.prefill; autosize(); $("#input").focus(); });
}

function routeText(r, agent) {
  if (!r || !Object.keys(r).length) return "";
  const parts = [];
  if (r.voters) parts.push("свели: " + r.voters);
  if (r.model) parts.push(r.model);
  const tiers = { fast: "быстрый ответ", code: "код", deep: "сложная задача", council: "консилиум", all: "все агенты" };
  if (r.tier && tiers[r.tier]) parts.push(tiers[r.tier]);
  if (r.cached) parts.push("из кэша, квота не тратилась");
  if (r.tried && r.tried.length) parts.push("резерв после сбоев: " + r.tried.length);
  return parts.join(" · ");
}

function renderMsg(m) {
  if (m.role === "user") {
    const att = (m.attachments || []).length ? `<span class="att">📎 ${m.attachments.map(esc).join(", ")}</span>` : "";
    return `<div class="msg user"><div class="bubble">${esc(m.shown ?? m.content)}${att}</div></div>`;
  }
  const agent = m.agent || "mind";
  const who = (m.route && m.route.agent && m.route.agent !== "all" && agent === "all") ? m.route.agent : agent;
  const acts = (m.activity || []).map((a) => `<span class="act">${ICONS.tool}${esc(a)}</span>`).join("");
  const body = m.error ? `<div class="err">${esc(m.error)}</div>` : `<div class="md">${MD.render(m.content)}</div>`;
  return `<div class="msg assistant">
    <div class="who"><span class="ic ${who}" style="background:${""}">${ICONS[who] || ICONS.mind}</span><b>${esc(agentMeta(agent).title)}</b>
      <span class="route">${esc(routeText(m.route, agent))}</span></div>
    ${acts ? `<div class="activity">${acts}</div>` : ""}${body}
    <div class="msg-tools"><button data-copy-msg>Копировать</button></div></div>`;
}

function bindCode(root) {
  $$("[data-copy]", root).forEach((b) => b.onclick = () => {
    navigator.clipboard.writeText(b.closest(".code").querySelector("code").dataset.raw.replace(/&quot;/g, '"')
      .replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&"));
    b.textContent = "Скопировано"; setTimeout(() => b.textContent = "Копировать", 1400);
  });
  $$("[data-copy-msg]", root).forEach((b) => b.onclick = () => {
    const el = b.closest(".msg").querySelector(".md");
    navigator.clipboard.writeText(el ? el.innerText : ""); toast("Ответ скопирован");
  });
}

function scrollDown(force = false) {
  const sc = $("#scroll");
  if (force || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 160) requestAnimationFrame(() => sc.scrollTop = sc.scrollHeight);
}

// ---------------------------------------------------------------- отправка
async function send() {
  if (S.busy) return stop();
  const input = $("#input");
  const text = input.value.trim();
  if (!text && !S.attachments.length) return;
  if (S.chat.readonly) return;
  if (!S.chat.id) {
    const created = await api("chats", { method: "POST", body: { project: S.chat.project, project_path: S.chat.project_path, agent: S.agent } });
    S.chat = created;
  }
  input.value = ""; autosize();
  const attachments = S.attachments; S.attachments = []; renderChips();
  S.chat.messages.push({ role: "user", content: text, shown: text, attachments: attachments.map((a) => a.name) });
  const ans = { role: "assistant", agent: S.agent, content: "", route: {}, activity: [] };
  S.chat.messages.push(ans);
  const feed = $("#feed");
  if (S.chat.messages.length === 2) feed.innerHTML = "";
  feed.insertAdjacentHTML("beforeend", renderMsg(S.chat.messages[S.chat.messages.length - 2]));
  feed.insertAdjacentHTML("beforeend", `<div class="msg assistant" id="live">${liveInner(ans)}</div>`);
  scrollDown(true);
  setBusy(true);

  const body = { text, agent: S.agent, mode: S.mode, skills: S.skills, attachments };
  try {
    const r = await fetch(`/api/chats/${S.chat.id}/send`, {
      method: "POST", headers: { "X-Studio-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok || !r.body) throw new Error("сервер не ответил");
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "", pending = false;
    const paint = () => { pending = false; const live = $("#live"); if (live) { live.innerHTML = liveInner(ans); bindCode(live); scrollDown(); } };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        const ev = (chunk.match(/^event: (.*)$/m) || [])[1];
        const raw = (chunk.match(/^data: (.*)$/m) || [])[1];
        if (!ev || raw === undefined) continue;
        const data = JSON.parse(raw);
        if (ev === "text") ans.content += data;
        else if (ev === "route") ans.route = data;
        else if (ev === "activity") ans.activity.push(data);
        else if (ev === "error") ans.error = data;
        else if (ev === "chat") { S.chat.title = data.title; crumbs([S.chat.project || "Mind Studio", data.title]); }
        if (!pending) { pending = true; requestAnimationFrame(paint); }
      }
    }
  } catch (e) {
    ans.error = "Связь с Mind Studio прервалась: " + e.message;
  }
  setBusy(false);
  const live = $("#live");
  if (live) { live.outerHTML = renderMsg(ans); feed.lastElementChild.style.animation = "none"; bindCode(feed); }
  if (!ans.content && !ans.error) toast("Ответ пустой — попробуйте другой режим или агента");
  loadSide();
}

function liveInner(m) {
  const html = renderMsg(m);
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  const inner = tmp.firstElementChild;
  if (!m.content && !m.error) {
    const md = inner.querySelector(".md");
    if (md) md.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  }
  return inner.innerHTML;
}

async function stop() {
  if (S.chat && S.chat.id) await api(`chats/${S.chat.id}/stop`, { method: "POST" }).catch(() => {});
}

function setBusy(b) {
  S.busy = b;
  $("#btn-send").classList.toggle("busy", b);
  $("#btn-send").title = b ? "Остановить" : "Отправить (Enter)";
  $("#hint").textContent = b ? agentMeta(S.agent).title + " отвечает…" : "";
}

// ---------------------------------------------------------------- выбор агента, режима, скиллов
function openMenu(anchor, html, onPick) {
  const menu = $("#menu");
  menu.innerHTML = html;
  menu.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  const h = Math.min(menu.scrollHeight, innerHeight * 0.6);
  menu.style.left = Math.min(r.left, innerWidth - menu.offsetWidth - 12) + "px";
  menu.style.top = (r.top - h - 8 > 8 ? r.top - h - 8 : r.bottom + 8) + "px";
  $$(".mi", menu).forEach((b) => b.onclick = (e) => { e.stopPropagation(); onPick(b.dataset.v); });
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
}
function closeMenu() { $("#menu").classList.add("hidden"); }

function renderPickers() {
  const a = agentMeta(S.agent);
  $("#pick-agent").innerHTML = `<span class="sw ic ${S.agent}" style="display:grid;place-items:center;color:#fff"></span>${esc(a.title)}${ICONS.caret}`;
  const m = S.state.modes.find((x) => x.id === S.mode) || S.state.modes[0];
  $("#pick-mode").innerHTML = `${esc(m.name)}${ICONS.caret}`;
  $("#pick-mode").parentElement.classList.toggle("hidden", !["mind", "all"].includes(S.agent));
  $("#pick-skill").textContent = S.skills.length ? `Скиллы: ${S.skills.length}` : "Скиллы";
}

function agentMenu() {
  const st = S.state.status;
  openMenu($("#pick-agent"), '<div class="mh">Кто отвечает</div>' + S.state.agents.map((a) => {
    const ready = a.id === "all" || st[a.id].ready;
    return `<button class="mi${ready ? "" : " off"}" data-v="${a.id}"><span class="ic ${a.id}"></span>
      <span><div class="n">${esc(a.title)}</div><div class="s">${esc(ready ? a.subtitle : st[a.id].detail)}</div></span>
      ${S.agent === a.id ? `<span class="chk">${ICONS.check}</span>` : ""}</button>`;
  }).join(""), (v) => {
    if (v === "antigravity" && !st.antigravity.ready) { closeMenu(); return showAgents(); }
    S.agent = v; closeMenu(); renderPickers(); if (!S.chat.messages.length) renderChat();
  });
}

function modeMenu() {
  openMenu($("#pick-mode"), '<div class="mh">Режим Mind</div>' + S.state.modes.map((m) =>
    `<button class="mi" data-v="${m.id}"><span><div class="n">${esc(m.name)}</div><div class="s">${esc(m.hint)}</div></span>
      ${S.mode === m.id ? `<span class="chk">${ICONS.check}</span>` : ""}</button>`).join(""),
  (v) => { S.mode = v; closeMenu(); renderPickers(); });
}

function skillMenu(anchor = $("#pick-skill")) {
  const list = S.state.skills;
  if (!list.length) { toast("Скиллы не найдены. Положите SKILL.md в ~/.config/opencode/skills/имя/"); return; }
  openMenu(anchor, '<div class="mh">Скиллы — подключаются к запросу</div>' + list.map((s) =>
    `<button class="mi" data-v="${esc(s.id)}"><span><div class="n">${esc(s.name)} <span class="badge">${esc(s.source)}</span></div>
      <div class="s">${esc(s.description.slice(0, 120))}</div></span>
      ${S.skills.includes(s.id) ? `<span class="chk">${ICONS.check}</span>` : ""}</button>`).join(""),
  (v) => { toggleSkill(v); closeMenu(); });
}

function toggleSkill(id) {
  S.skills = S.skills.includes(id) ? S.skills.filter((x) => x !== id) : [...S.skills, id];
  renderChips(); renderPickers();
}

function renderChips() {
  $("#chips").innerHTML = [
    ...S.skills.map((s) => `<span class="chip">★ ${esc(s)}<button data-unskill="${esc(s)}">${ICONS.x}</button></span>`),
    ...S.attachments.map((a, i) => `<span class="chip">📎 ${esc(a.name)}<button data-unatt="${i}">${ICONS.x}</button></span>`),
  ].join("");
  $$("[data-unskill]").forEach((b) => b.onclick = () => toggleSkill(b.dataset.unskill));
  $$("[data-unatt]").forEach((b) => b.onclick = () => { S.attachments.splice(+b.dataset.unatt, 1); renderChips(); });
}

// ---------------------------------------------------------------- панели
function showHistory() {
  show("history"); crumbs(["История"]);
  $("#panel").innerHTML = `<div class="panel-head"><h1>История</h1><p>Все разговоры Mind Studio.</p></div>
    <input class="search" id="hsearch" placeholder="Поиск по названию…">
    <div id="hlist"></div>`;
  const draw = (q = "") => {
    const items = S.chats.filter((c) => c.title.toLowerCase().includes(q.toLowerCase()));
    $("#hlist").innerHTML = items.map((c) => `<div class="list-row" data-open="${c.id}">
      <span class="ic ${c.agent || "mind"}" style="width:24px;height:24px;border-radius:7px;display:grid;place-items:center;color:#fff">${ICONS[c.agent] || ICONS.mind}</span>
      <span class="t">${esc(c.title)}</span><span class="m">${esc(c.project || "")}</span><span class="m">${ago(c.updated)}</span></div>`).join("")
      || '<p style="color:var(--muted)">Пока пусто.</p>';
    $$("[data-open]").forEach((r) => r.onclick = () => openChat(r.dataset.open));
  };
  draw();
  $("#hsearch").oninput = (e) => draw(e.target.value);
}

function showSkills() {
  show("skills"); crumbs(["Скиллы"]);
  const list = S.state.skills;
  $("#panel").innerHTML = `<div class="panel-head"><h1>Скиллы</h1>
    <p>Инструкции из OpenCode, Claude Code, Antigravity и AIsktagOS. Подключённый скилл уходит агенту вместе с вопросом.</p></div>
    <div class="grid">${list.map((s) => `<div class="card"><h3>${esc(s.name)} <span class="badge">${esc(s.source)}</span></h3>
      <p>${esc(s.description || "Без описания")}</p><div class="actions">
      <button class="btn ${S.skills.includes(s.id) ? "" : "primary"}" data-sk="${esc(s.id)}">${S.skills.includes(s.id) ? "Отключить" : "Подключить"}</button></div></div>`).join("")
      || "<p>Скиллы не найдены.</p>"}</div>`;
  $$("[data-sk]").forEach((b) => b.onclick = () => { toggleSkill(b.dataset.sk); showSkills(); });
}

function linkAsk() {
  return "Запусти в терминале в фоновом режиме (как демон, не жди завершения) и оставь работать команду:\n" + (S.state.link_command || "");
}

function showAgents() {
  show("agents"); crumbs(["Агенты"]);
  const st = S.state.status;
  const prov = S.state.providers.map((p) => `<span class="badge ok">${esc(p.title)}</span>`).join(" ") || '<span class="badge off">нет</span>';
  $("#panel").innerHTML = `<div class="panel-head"><h1>Агенты</h1><p>Кто может отвечать в Mind Studio и как их подключить.</p></div>
  <div class="grid">
    <div class="card"><h3><span class="ic mind" style="width:24px;height:24px;border-radius:7px;display:grid;place-items:center;color:#fff">${ICONS.mind}</span>Mind
      <span class="badge ${st.mind.ready ? "ok" : "off"}">${st.mind.ready ? "готов" : "нет моделей"}</span></h3>
      <p>Ваши модели через маршрутизатор: бесплатные и локальные первыми, резерв при лимите, кэш.</p>
      <p style="margin-top:8px">${prov}</p><div class="actions"><button class="btn" data-open-settings="keys">Ключи моделей</button></div></div>
    <div class="card"><h3><span class="ic claude" style="width:24px;height:24px;border-radius:7px;display:grid;place-items:center;color:#fff">${ICONS.claude}</span>Claude
      <span class="badge ${st.claude.ready ? "ok" : "off"}">${st.claude.ready ? "готов" : "не найден"}</span></h3>
      <p>Claude Code на вашей подписке. Разговор продолжает одну сессию; в папке проекта видит ваш код.</p>
      <div class="actions"><button class="btn" data-open-settings="agents">Настроить</button></div></div>
    <div class="card"><h3><span class="ic antigravity" style="width:24px;height:24px;border-radius:7px;display:grid;place-items:center;color:#fff">${ICONS.antigravity}</span>Antigravity
      <span class="badge ${st.antigravity.ready ? "ok" : "off"}">${st.antigravity.ready ? "подключён" : "мост не запущен"}</span></h3>
      ${st.antigravity.autostart
        ? `<p>Агент Antigravity отвечает прямо здесь. Мост запускается сам вместе с Antigravity. Если он не подключился, перезапустите Antigravity или отправьте <b>в чат Antigravity</b> эту просьбу:</p>`
        : `<p>Агент Antigravity отвечает прямо здесь. Нажмите «Запускать автоматически» и перезапустите Antigravity — мост будет подниматься сам. Или один раз за сеанс отправьте <b>в чат Antigravity</b> эту просьбу:</p>`}
      <pre class="howto">${esc(linkAsk())}</pre>
      ${st.antigravity.ready ? "" : `<p style="margin-top:8px;color:var(--muted);font-size:12.5px">${esc(st.antigravity.detail)}</p>`}
      <div class="actions"><button class="btn primary" id="copy-link">Скопировать просьбу</button><button class="btn" id="recheck">Проверить</button>${st.antigravity.autostart || !st.antigravity.installed ? "" : '<button class="btn" id="agy-autostart">Запускать автоматически</button>'}</div></div>
  </div>`;
  $$("[data-open-settings]").forEach((b) => b.onclick = () => openSettings(b.dataset.openSettings));
  $("#copy-link").onclick = () => { navigator.clipboard.writeText(linkAsk()); toast("Скопировано — вставьте в чат Antigravity"); };
  const auto = $("#agy-autostart");
  if (auto) auto.onclick = async () => {
    try { await api("antigravity/autostart", { method: "POST", body: {} }); await refreshState(); showAgents(); toast("Готово — перезапустите Antigravity"); }
    catch (e) { toast("Не удалось: " + e.message); }
  };
  $("#recheck").onclick = async () => { await refreshState(); showAgents(); toast(S.state.status.antigravity.ready ? "Antigravity подключён" : "Мост пока не отвечает"); };
}

// ---------------------------------------------------------------- настройки
const SETTINGS_TABS = [
  ["general", "Общие"], ["keys", "Модели и ключи"], ["agents", "Агенты"], ["projects", "Проекты"], ["about", "О программе"],
];

async function openSettings(tab = "general") {
  S.settings = await api("settings");
  $("#settings").classList.remove("hidden");
  $("#settings-nav").innerHTML = SETTINGS_TABS.map(([id, n]) => `<button class="nav-item${id === tab ? " active" : ""}" data-tab="${id}"><span>${n}</span></button>`).join("");
  $$("[data-tab]").forEach((b) => b.onclick = () => openSettings(b.dataset.tab));
  $("#settings-title").textContent = SETTINGS_TABS.find((t) => t[0] === tab)[1];
  const st = S.settings;
  const sw = (id, on) => `<button class="switch${on ? " on" : ""}" data-sw="${id}"></button>`;
  let html = "";
  if (tab === "general") {
    html = `<div class="group">Оформление</div><div class="set-card">
      <div class="set-row"><div class="l"><b>Тема</b><span>Светлая как в Antigravity или тёмная Aurora</span></div>
        <div class="seg">${["light", "dark"].map((t) => `<button data-theme-set="${t}" class="${S.theme === t ? "on" : ""}">${t === "light" ? "Светлая" : "Тёмная"}</button>`).join("")}</div></div></div>
      <div class="group">Экономия квоты</div><div class="set-card">
      <div class="set-row"><div class="l"><b>Режим «Авто» для Mind</b><span>Модель под задачу: бесплатные первыми, платные последними</span></div>${sw("auto", st.auto)}</div>
      <div class="set-row"><div class="l"><b>Кэш ответов</b><span>Повторный вопрос в течение суток — без обращения к API</span></div>${sw("cache", st.cache)}</div>
      <div class="set-row"><div class="l"><b>Сбросить паузы и кэш</b><span>Если модель снова доступна раньше срока</span></div><button class="btn" id="clear-cache">Сбросить</button></div></div>`;
  } else if (tab === "keys") {
    html = `<div class="group">Ключи API — хранятся в ${esc(st.config_path)} и в окне не показываются</div><div class="set-card">` +
      Object.entries(st.keys).map(([id, k]) => `<div class="set-row"><div class="l"><b>${esc(k.title)} <span class="badge ${k.free ? "ok" : ""}">${k.free ? "бесплатно" : "платно"}</span></b>
        <span>${esc(k.description)}</span></div>
        <input type="password" data-key="${id}" placeholder="${k.env ? "задан в " + esc(k.var) : k.saved ? "сохранён · вставьте новый, чтобы заменить" : "вставьте ключ"}"></div>`).join("") +
      `</div><div class="save-bar"><button class="btn primary" id="save-keys">Сохранить ключи</button></div>`;
  } else if (tab === "agents") {
    html = `<div class="group">Claude Code</div><div class="set-card">
      <div class="set-row"><div class="l"><b>Разрешить Claude править файлы проекта</b><span>Иначе Claude только читает и отвечает. Правки идут в папку проекта разговора.</span></div>${sw("claude_edit", st.claude_edit)}</div>
      <div class="set-row"><div class="l"><b>Модель Claude</b><span>Пусто — модель по умолчанию вашей подписки</span></div>
        <div class="seg">${[["", "Авто"], ["haiku", "Haiku"], ["sonnet", "Sonnet"], ["opus", "Opus"]].map(([v, n]) => `<button data-cm="${v}" class="${(st.claude_model || "") === v ? "on" : ""}">${n}</button>`).join("")}</div></div></div>
      <div class="group">Antigravity</div><div class="set-card"><div class="set-row"><div class="l"><b>Мост</b>
        <span>${esc(S.state.status.antigravity.detail)}</span></div><button class="btn" id="go-agents">Как подключить</button></div></div>`;
  } else if (tab === "projects") {
    html = `<div class="group">Папки проектов — в них работает Claude, они видны в боковой панели</div><div class="set-card">` +
      st.projects.map((p, i) => `<div class="set-row"><div class="l"><b>${esc(p.name)}</b><span>${esc(p.path)}</span></div><button class="btn" data-rmp="${i}">Убрать</button></div>`).join("") +
      `<div class="set-row"><div class="l"><b>Добавить папку</b><span>Полный путь, например C:\\Users\\user\\projects\\site</span></div>
        <input type="text" id="new-proj" placeholder="Путь к папке"><button class="btn" id="add-proj">Добавить</button></div></div>`;
  } else {
    html = `<div class="set-card"><div class="set-row"><div class="l"><b>Mind Studio</b><span>Часть AIsktagOS. Один разговор — ваши модели, Claude и Antigravity.</span></div></div>
      <div class="set-row"><div class="l"><b>Настройки</b><span>${esc(st.config_path)}</span></div></div></div>`;
  }
  $("#settings-content").innerHTML = html;
  bindSettings(tab);
}

function bindSettings(tab) {
  const save = async (data, msg = "Сохранено") => { S.settings = await api("settings", { method: "POST", body: data }); toast(msg); await refreshState(); };
  $$("[data-sw]").forEach((b) => b.onclick = async () => { const on = !b.classList.contains("on"); b.classList.toggle("on", on); await save({ [b.dataset.sw]: on }); });
  $$("[data-theme-set]").forEach((b) => b.onclick = () => { S.theme = b.dataset.themeSet; applyTheme(); save({ theme: S.theme }, "Тема изменена"); openSettings("general"); });
  $$("[data-cm]").forEach((b) => b.onclick = async () => { await save({ claude_model: b.dataset.cm }); openSettings("agents"); });
  const cc = $("#clear-cache"); if (cc) cc.onclick = async () => { const r = await api("cache/clear", { method: "POST" }); toast(`Сброшено, ответов в кэше удалено: ${r.cleared}`); };
  const sk = $("#save-keys"); if (sk) sk.onclick = async () => {
    const keys = {}; $$("[data-key]").forEach((i) => { if (i.value.trim()) keys[i.dataset.key] = i.value.trim(); });
    if (!Object.keys(keys).length) return toast("Введите хотя бы один ключ");
    await save({ keys }, "Ключи сохранены"); openSettings("keys");
  };
  const ga = $("#go-agents"); if (ga) ga.onclick = () => { closeSettings(); showAgents(); };
  $$("[data-rmp]").forEach((b) => b.onclick = async () => { const p = S.settings.projects.filter((_, i) => i !== +b.dataset.rmp); await save({ projects: p }); openSettings("projects"); loadSide(); });
  const ap = $("#add-proj"); if (ap) ap.onclick = async () => {
    const path = $("#new-proj").value.trim(); if (!path) return;
    const before = S.settings.projects.length;
    await save({ projects: [...S.settings.projects, { path }] });
    if (S.settings.projects.length === before) toast("Такой папки нет"); openSettings("projects"); loadSide();
  };
}
function closeSettings() { $("#settings").classList.add("hidden"); }

// ---------------------------------------------------------------- ввод
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, innerHeight * 0.4) + "px"; }

async function refreshState() {
  S.state = await api("state");
  renderHealth(); renderPickers();
  $("#skills-count").textContent = S.state.skills.length || "";
}

async function init() {
  applyTheme();
  try { await refreshState(); } catch (e) {
    document.body.innerHTML = `<div style="padding:40px;font:15px system-ui">Mind Studio не смог связаться со своим сервером: ${esc(e.message)}</div>`;
    return;
  }
  const saved = await api("settings").catch(() => ({}));
  if (saved.theme && !localStorage.getItem("theme")) { S.theme = saved.theme; applyTheme(); }
  newChat();
  loadSide(); loadAgy();

  $("#btn-new").onclick = () => newChat();
  $("[data-view=history]").onclick = showHistory;
  $("[data-view=skills]").onclick = showSkills;
  $("[data-view=agents]").onclick = showAgents;
  $("#btn-settings").onclick = () => openSettings();
  $("#settings-close").onclick = closeSettings;
  $("#settings").onclick = (e) => { if (e.target.id === "settings") closeSettings(); };
  $("#btn-theme").onclick = () => { S.theme = S.theme === "dark" ? "light" : "dark"; applyTheme(); };
  $("#btn-toggle-side").onclick = () => $("#app").classList.toggle("side-hidden");
  $("#pick-agent").onclick = (e) => { e.stopPropagation(); agentMenu(); };
  $("#pick-mode").onclick = (e) => { e.stopPropagation(); modeMenu(); };
  $("#pick-skill").onclick = (e) => { e.stopPropagation(); skillMenu(); };
  $("#btn-send").onclick = send;
  $("#btn-attach").onclick = () => $("#file").click();
  $("#file").onchange = async (e) => {
    for (const f of e.target.files) {
      if (f.size > 2_000_000) { toast(`${f.name}: больше 2 МБ — пропущен`); continue; }
      S.attachments.push({ name: f.name, text: await f.text() });
    }
    e.target.value = ""; renderChips();
  };
  const input = $("#input");
  input.oninput = () => {
    autosize();
    const v = input.value;
    if (v === "/") { input.value = ""; skillMenu($("#pick-skill")); }
    if (v === "@") { input.value = ""; agentMenu(); }
  };
  input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } };
  input.addEventListener("paste", (e) => {
    const t = e.clipboardData.getData("text");
    if (t.length > 4000) { e.preventDefault(); S.attachments.push({ name: `вставка ${t.length} симв.`, text: t }); renderChips(); toast("Длинный текст добавлен вложением"); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "n") { e.preventDefault(); newChat(); }
    if (e.key === "Escape") { closeMenu(); closeSettings(); }
  });
  $("#scroll").addEventListener("scroll", (e) => $(".main").classList.toggle("scrolled", e.target.scrollTop > 4));
  setInterval(async () => { try { const st = S.state.status.antigravity.ready; await refreshState(); if (st !== S.state.status.antigravity.ready) loadAgy(); } catch (_) { /* сервер перезапускается */ } }, 15000);
}

init();
