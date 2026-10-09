// Дымовой тест страницы для агента Mind: node smoke.js page.html → JSON {errors, clickable, changed, alerts}.
// Маленький поддельный DOM без зависимостей: запускаем скрипты страницы, жмём на всё кликабельное и смотрим,
// падает ли код и меняется ли страница. Так Mind сам замечает «игра не реагирует на клики» и чинит её.
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const errors = [];
const alerts = [];
const VOID = new Set("area base br col embed hr img input link meta source track wbr".split(" "));

class ClassList {
  constructor(el) { this.el = el; }
  _get() { return (this.el.attrs.class || "").split(/\s+/).filter(Boolean); }
  _set(a) { this.el.attrs.class = [...new Set(a)].join(" "); }
  add(...c) { this._set(this._get().concat(c)); }
  remove(...c) { this._set(this._get().filter((x) => !c.includes(x))); }
  toggle(c, force) { const on = force === undefined ? !this.contains(c) : force; on ? this.add(c) : this.remove(c); return on; }
  contains(c) { return this._get().includes(c); }
  replace(a, b) { this.remove(a); this.add(b); }
  get length() { return this._get().length; }
  forEach(f) { this._get().forEach(f); }
  toString() { return this.el.attrs.class || ""; }
}

class Node_ {
  constructor(tag) {
    this.tagName = (tag || "#text").toUpperCase(); this.nodeName = this.tagName; this.attrs = {}; this.children = [];
    this.parentNode = null; this.listeners = {}; this.style = {}; this.dataset = {}; this._text = "";
    this.classList = new ClassList(this); this.value = ""; this.checked = false; this.disabled = false;
    this.nodeType = tag ? 1 : 3;
  }
  get id() { return this.attrs.id || ""; } set id(v) { this.attrs.id = String(v); }
  get className() { return this.attrs.class || ""; } set className(v) { this.attrs.class = String(v); }
  get parentElement() { return this.parentNode; }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; } get lastChild() { return this.children[this.children.length - 1] || null; }
  get firstElementChild() { return this.children.find((c) => c.nodeType === 1) || null; }
  get nextElementSibling() { const s = this.parentNode ? this.parentNode.children : []; return s[s.indexOf(this) + 1] || null; }
  get previousElementSibling() { const s = this.parentNode ? this.parentNode.children : []; return s[s.indexOf(this) - 1] || null; }
  get childElementCount() { return this.children.length; }
  get textContent() { return this.nodeType === 3 ? this._text : this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { if (this.nodeType === 3) this._text = String(v); else { this.children = []; if (v !== "" && v != null) this.appendChild(textNode(String(v))); } }
  get innerText() { return this.textContent; } set innerText(v) { this.textContent = v; }
  get innerHTML() { return this.children.map(serialize).join(""); }
  set innerHTML(v) { this.children = []; parseInto(this, String(v)); }
  get outerHTML() { return serialize(this); }
  set onclick(f) { this._onclick = f; } get onclick() { return this._onclick || null; }
  setAttribute(k, v) { k = k.toLowerCase(); this.attrs[k] = String(v); if (k.startsWith("data-")) this.dataset[camel(k.slice(5))] = String(v); if (k === "value") this.value = String(v); }
  getAttribute(k) { return this.attrs[k.toLowerCase()] ?? null; }
  hasAttribute(k) { return k.toLowerCase() in this.attrs; }
  removeAttribute(k) { delete this.attrs[k.toLowerCase()]; }
  toggleAttribute(k, f) { const on = f === undefined ? !this.hasAttribute(k) : f; on ? this.setAttribute(k, "") : this.removeAttribute(k); return on; }
  appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(typeof c === "string" ? textNode(c) : c)); }
  prepend(...cs) { cs.reverse().forEach((c) => this.insertBefore(typeof c === "string" ? textNode(c) : c, this.children[0] || null)); }
  insertBefore(c, ref) { if (c.parentNode) c.remove(); c.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; }
  insertAdjacentHTML(pos, h) { const tmp = new Node_("div"); parseInto(tmp, h); const kids = tmp.children.slice(); if (pos === "beforeend") kids.forEach((k) => this.appendChild(k)); else if (pos === "afterbegin") kids.reverse().forEach((k) => this.insertBefore(k, this.children[0] || null)); else if (this.parentNode) kids.forEach((k) => this.parentNode.insertBefore(k, pos === "beforebegin" ? this : this.nextElementSibling)); }
  insertAdjacentElement(pos, el) { if (pos === "beforeend") this.appendChild(el); else this.insertBefore(el, this.children[0] || null); return el; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  replaceChild(n, o) { const i = this.children.indexOf(o); if (i >= 0) { this.children[i] = n; n.parentNode = this; o.parentNode = null; } return o; }
  replaceChildren(...cs) { this.children = []; this.append(...cs); }
  replaceWith(n) { if (this.parentNode) this.parentNode.replaceChild(n, this); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  cloneNode(deep) { const c = new Node_(this.nodeType === 3 ? null : this.tagName); c._text = this._text; c.attrs = { ...this.attrs }; c.style = { ...this.style }; c.dataset = { ...this.dataset }; if (deep) this.children.forEach((k) => c.appendChild(k.cloneNode(true))); return c; }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  dispatchEvent(e) { fire(this, e.type, e); return true; }
  click() { fire(this, "click"); }
  focus() {} blur() {} select() {} scrollIntoView() {} animate() { return { finished: Promise.resolve(), onfinish: null, cancel() {} }; }
  getBoundingClientRect() { return { x: 0, y: 0, top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100 }; }
  get offsetWidth() { return 100; } get offsetHeight() { return 100; } get clientWidth() { return 100; } get clientHeight() { return 100; }
  getContext() { return canvasCtx(); } toDataURL() { return "data:,"; }
  matches(sel) { return sel.split(",").some((s) => matchOne(this, s.trim())); }
  closest(sel) { for (let x = this; x && x.nodeType === 1; x = x.parentNode) if (x.matches && x.matches(sel)) return x; return null; }
  querySelectorAll(sel) { const out = []; walk(this, (n) => { if (n !== this && n.nodeType === 1 && n.matches(sel)) out.push(n); }); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByClassName(c) { return this.querySelectorAll("." + c); }
  getElementsByTagName(t) { return this.querySelectorAll(t); }
}

function camel(s) { return s.replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }
function textNode(t) { const n = new Node_(null); n._text = t; return n; }
function walk(n, f) { f(n); n.children.forEach((c) => walk(c, f)); }
function serialize(n) {
  if (n.nodeType === 3) return n._text;
  const a = Object.entries(n.attrs).map(([k, v]) => ` ${k}="${v}"`).join("");
  const st = Object.entries(n.style).filter(([, v]) => v !== "" && typeof v !== "function").map(([k, v]) => `${k}:${v}`).join(";");
  return `<${n.tagName.toLowerCase()}${a}${st ? ` style~="${st}"` : ""}>${n.children.map(serialize).join("")}</${n.tagName.toLowerCase()}>`;
}
function matchOne(n, sel) {
  const last = sel.split(/\s+|>/).filter(Boolean).pop() || "";
  const m = last.match(/^([a-z0-9*]+)?((?:[#.][\w-]+|\[[^\]]+\]|:[\w-]+(?:\([^)]*\))?)*)$/i);
  if (!m) return false;
  if (m[1] && m[1] !== "*" && m[1].toUpperCase() !== n.tagName) return false;
  for (const part of m[2].match(/[#.][\w-]+|\[[^\]]+\]|:[\w-]+(?:\([^)]*\))?/g) || []) {
    if (part[0] === "#" && n.id !== part.slice(1)) return false;
    if (part[0] === "." && !n.classList.contains(part.slice(1))) return false;
    if (part[0] === "[") { const [k, v] = part.slice(1, -1).split("="); if (!n.hasAttribute(k.trim()) && !(k.startsWith("data-") && camel(k.slice(5)) in n.dataset)) return false; if (v !== undefined && n.getAttribute(k.trim()) !== v.replace(/["']/g, "")) return false; }
    if (part === ":not(.empty)" && n.classList.contains("empty")) return false;
  }
  return true;
}
function parseInto(root, src) {
  const re = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+|<)/g;
  let cur = root, m;
  while ((m = re.exec(src))) {
    if (m[5] !== undefined) { if (m[5].trim() || cur !== root) cur.appendChild(textNode(m[5].replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">"))); continue; }
    if (!m[2]) continue;
    const tag = m[2].toLowerCase();
    if (m[1]) { for (let x = cur; x && x !== root; x = x.parentNode) if (x.tagName === tag.toUpperCase()) { cur = x.parentNode; break; } continue; }
    const el = new Node_(tag);
    for (const a of (m[3] || "").matchAll(/([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
      const k = a[1].toLowerCase(), v = a[2] ?? a[3] ?? a[4] ?? "";
      if (k === "style") v.split(";").forEach((d) => { const [p, ...r] = d.split(":"); if (p && r.length) el.style[camel(p.trim())] = r.join(":").trim(); });
      else el.setAttribute(k, v);
    }
    cur.appendChild(el);
    if (tag === "script" || tag === "style") { const end = src.toLowerCase().indexOf(`</${tag}`, re.lastIndex); el.appendChild(textNode(src.slice(re.lastIndex, end < 0 ? src.length : end))); re.lastIndex = end < 0 ? src.length : src.indexOf(">", end) + 1; continue; }
    if (!VOID.has(tag) && !m[4]) cur = el;
  }
}
function canvasCtx() { return new Proxy({ canvas: { width: 300, height: 150 } }, { get: (t, k) => k in t ? t[k] : k === "measureText" ? () => ({ width: 10 }) : k.startsWith("create") ? () => ({ addColorStop() {} }) : k === "getImageData" ? () => ({ data: new Uint8ClampedArray(4) }) : () => {}, set: (t, k, v) => { t[k] = v; return true; } }); }

// --- окружение
const timers = [];
const doc = new Node_("#document"); doc.nodeType = 9;
const htmlEl = new Node_("html"); doc.appendChild(htmlEl);
parseInto(htmlEl, html.replace(/^[\s\S]*?<html[^>]*>/i, ""));
const body = doc.querySelector("body") || htmlEl;
const head = doc.querySelector("head") || htmlEl;
Object.assign(doc, {
  body, head, documentElement: htmlEl, readyState: "loading",
  createElement: (t) => new Node_(t), createElementNS: (_, t) => new Node_(t), createTextNode: textNode,
  createDocumentFragment: () => new Node_("fragment"), getElementById: (id) => doc.querySelector("#" + id),
  hidden: false, visibilityState: "visible", activeElement: body, title: "",
});
function fire(el, type, ev) {
  ev = ev || {};
  const e = Object.assign({ type, target: el, currentTarget: el, key: "", code: "", clientX: 50, clientY: 50, touches: [{ clientX: 50, clientY: 50 }], changedTouches: [{ clientX: 50, clientY: 50 }], preventDefault() {}, stopPropagation() { this._stop = true; } }, ev);
  for (let x = el; x && !e._stop; x = x.parentNode) {
    e.currentTarget = x;
    const prop = x["on" + type] || (x === el && x.attrs && x.attrs["on" + type] ? inline(x, x.attrs["on" + type]) : null);
    const fns = (x.listeners[type] || []).concat(prop ? [prop] : []);
    for (const f of fns) try { f.call(x, e); } catch (err) { errors.push(`${type}: ${err && err.stack ? err.stack.split("\n").slice(0, 2).join(" ") : err}`); }
  }
}
const ctx = {
  document: doc, console: { log() {}, warn() {}, info() {}, debug() {}, error: (...a) => errors.push("console.error: " + a.join(" ")) },
  alert: (m) => alerts.push(String(m)), confirm: () => true, prompt: () => "",
  setTimeout: (f, ms) => { timers.push({ f, ms: ms || 0, every: false }); return timers.length; },
  setInterval: (f, ms) => { timers.push({ f, ms: ms || 0, every: true }); return timers.length; },
  clearTimeout: (i) => { if (timers[i - 1]) timers[i - 1].f = null; }, clearInterval: (i) => { if (timers[i - 1]) timers[i - 1].f = null; },
  requestAnimationFrame: (f) => { timers.push({ f: () => f(16), ms: 16, every: false }); return timers.length; }, cancelAnimationFrame: () => {},
  localStorage: (() => { const s = {}; return { getItem: (k) => (k in s ? s[k] : null), setItem: (k, v) => { s[k] = String(v); }, removeItem: (k) => { delete s[k]; }, clear: () => {} }; })(),
  performance: { now: () => Date.now() }, navigator: { userAgent: "smoke", vibrate() {}, clipboard: { writeText: async () => {} } },
  location: { href: "file:///index.html", reload() {}, hash: "", search: "" }, history: { pushState() {}, replaceState() {} },
  innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1, matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  getComputedStyle: (el) => new Proxy(el.style, { get: (t, k) => (k === "getPropertyValue" ? () => "" : t[k] ?? "") }),
  Audio: function () { return { play: async () => {}, pause() {} }; }, AudioContext: function () { return new Proxy({}, { get: () => () => new Proxy({}, { get: () => () => {} }) }); },
  Image: function () { return new Node_("img"); }, Event: function (t) { return { type: t }; }, CustomEvent: function (t, o) { return { type: t, detail: o && o.detail }; },
  KeyboardEvent: function (t, o) { return { type: t, ...o }; }, MouseEvent: function (t, o) { return { type: t, ...o }; },
  ResizeObserver: function () { return { observe() {}, disconnect() {} }; }, IntersectionObserver: function () { return { observe() {}, disconnect() {} }; },
  MutationObserver: function () { return { observe() {}, disconnect() {} }; }, fetch: async () => ({ ok: false, json: async () => ({}), text: async () => "" }),
};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
const winListeners = {};
ctx.addEventListener = (t, f) => (winListeners[t] = winListeners[t] || []).push(f);
ctx.removeEventListener = () => {};
doc.addEventListener = (t, f) => { if (t === "DOMContentLoaded") (winListeners.DOMContentLoaded = winListeners.DOMContentLoaded || []).push(f); else Node_.prototype.addEventListener.call(doc, t, f); };
vm.createContext(ctx);
function inline(el, code) { try { return vm.runInContext(`(function(event){${code}\n})`, ctx); } catch (e) { errors.push("onclick: " + e.message); return null; } }
function runTimers(budget = 40) {
  for (let n = 0; n < budget; n++) {
    const live = timers.filter((t) => t.f);
    if (!live.length) return;
    const t = live.reduce((a, b) => (b.ms < a.ms ? b : a));
    const f = t.f; if (!t.every) t.f = null;
    try { f(); } catch (err) { errors.push("timer: " + (err && err.message)); }
    if (t.every) t.ms += 1000;
  }
}
for (const s of doc.querySelectorAll("script")) {
  if (s.getAttribute("src") || /module|json|babel/.test(s.getAttribute("type") || "")) continue;
  try { vm.runInContext(s.textContent, ctx, { timeout: 3000 }); } catch (err) { errors.push("script: " + (err && err.stack ? err.stack.split("\n").slice(0, 2).join(" ") : err)); }
}
doc.readyState = "complete";
for (const t of ["DOMContentLoaded", "load"]) {
  const fns = (winListeners[t] || []).concat(t === "load" && typeof ctx.onload === "function" ? [ctx.onload] : []);
  for (const f of fns) try { f({ type: t }); } catch (err) { errors.push(t + ": " + err.message); }
}
runTimers();

// --- кликаем по всему, что реагирует на клик (сначала по несколько раз, чтобы пройти простые сценарии)
const clickable = () => [doc, ...doc.querySelectorAll("*")].filter((n) => n.nodeType === 1 && (n.listeners.click || n._onclick || n.attrs.onclick || n.tagName === "BUTTON"));
const snap = () => serialize(body).replace(/\d+(\.\d+)?\s*(с|s|сек)\b/g, "");
const before = snap();
const visibleBefore = (function vis(n) { return n.nodeType === 3 ? n._text : ["SCRIPT", "STYLE", "TEMPLATE"].includes(n.tagName) ? "" : n.children.map(vis).join(" "); })(body);
let changed = false, clicks = 0;
for (let round = 0; round < 3 && clicks < 60; round++) {
  for (const el of clickable()) {
    if (clicks >= 60) break;
    if (!el.parentNode && el !== doc) continue;
    const s0 = snap();
    fire(el, "click"); fire(el, "mousedown"); fire(el, "mouseup"); fire(el, "pointerdown"); fire(el, "pointerup");
    runTimers(10); clicks++;
    if (snap() !== s0) changed = true;
  }
}
for (const key of ["ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight", " ", "Enter"]) {
  const s0 = snap();
  for (const f of (winListeners.keydown || []).concat(doc.listeners.keydown || [])) try { f({ type: "keydown", key, code: key, preventDefault() {} }); } catch (err) { errors.push("keydown: " + err.message); }
  runTimers(10);
  if (snap() !== s0) changed = true;
}
const visible = (n) => (n.nodeType === 3 ? n._text : ["SCRIPT", "STYLE", "TEMPLATE"].includes(n.tagName) ? "" : n.children.map(visible).join(" "));
const JUNK = /(?:^|[^\w])(undefined|NaN|null|\[object Object\])(?![\w])/;
const junk = visible(body).match(JUNK);
if (junk && !JUNK.test(visibleBefore)) errors.push(`после кликов на странице появилось «${junk[1]}» — значит, состояние (массив/переменная) не заполнено или считается неверно`);
process.stdout.write(JSON.stringify({ errors: [...new Set(errors)].slice(0, 8), clickable: clickable().length, clicks, changed: changed || snap() !== before, alerts: alerts.slice(0, 5) }));
