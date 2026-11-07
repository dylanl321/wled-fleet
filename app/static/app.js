// Lumen: a remote for the light the house is already making.
// Home is live ribbons grouped by Zone. Paint is a sheet over Home. Find and
// Maintenance are pushed screens you visit about once a month.
(function () {
  "use strict";

  const Fx = window.LumenFx;
  const $ = (selector, root = document) => root.querySelector(selector);
  const DPR = Math.min(window.devicePixelRatio || 1, 2.5);
  const CACHE_KEY = "lumen.cache.v1";
  const VIEW_KEY = "lumen.view.v1";
  const PREVIEW_MS = 150;
  const PREVIEW_MANY_MS = 400;
  const MANY = 8;
  const COMMIT_TT = 4; // 400 ms, sent to WLED so the strip and the screen move together
  const SWEEP_MS = 400;

  // ----- state ------------------------------------------------------------

  const S = {
    home: { property: "All lights", zones: [], lights: [], looks: [], favorites: [], attention: {}, effects: [], palettes: [] },
    lights: new Map(),
    selection: new Set(),
    multi: false,
    sheet: "closed", // closed | rest | full
    socket: null,
    connected: false,
    everConnected: false,
    seq: 0,
    outbox: [],
    screen: null,
    cachedAt: 0,
    frozenFrames: false,
  };

  const frames = new Map(); // light id -> { prev, cur, tPrev, tCur }
  const predictions = new Map(); // light id -> { look, bri, on, from, t0, dur, until, kind, dir }
  const briHistory = new Map(); // light id -> [{t, v}]
  const ribbonEls = new Map(); // light id -> { root, canvas, ctx, name, pct }
  const visible = new Set();
  let boardFrame = null; // { mac, data, t }

  // ----- small helpers ---------------------------------------------------

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value == null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else if (key === "style") node.setAttribute("style", value);
        else node.setAttribute(key, value === true ? "" : value);
      }
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
  const now = () => performance.now();
  const pct = (bri) => Math.round((bri / 255) * 100);
  const haptic = (kind) => {
    if (!navigator.vibrate) return;
    const ms = { light: 6, medium: 14, strong: 24, success: [10, 40, 16], warning: [24, 60, 24] }[kind] || 6;
    try { navigator.vibrate(ms); } catch (_) { /* not allowed until first tap */ }
  };

  async function api(path, options = {}) {
    const init = { credentials: "same-origin", ...options };
    if (init.body && typeof init.body !== "string" && !(init.body instanceof FormData)) {
      init.body = JSON.stringify(init.body);
      init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    }
    const response = await fetch(path, init);
    if (response.status === 401) {
      showLogin();
      throw new Error("Sign in required");
    }
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
    if (!response.ok) throw new Error((data && data.detail) || `Request failed (${response.status})`);
    return data;
  }

  function b64(text) {
    const raw = atob(text);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function b64encode(bytes) {
    let raw = "";
    for (let i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
    return btoa(raw);
  }

  function lightLook(light) {
    return { fx: light.fx, pal: light.pal, col: light.col, sx: light.sx, ix: light.ix };
  }

  function sameLook(a, b) {
    return a && b && a.fx === b.fx && a.pal === b.pal && JSON.stringify(a.col) === JSON.stringify(b.col);
  }

  function selected() {
    return [...S.selection].map((id) => S.lights.get(id)).filter(Boolean);
  }

  function lightCount(points) {
    return Math.max(1, Math.min(points || 1, 120));
  }

  // ----- cache and view state -------------------------------------------

  function saveCache() {
    try {
      const snapshot = {};
      for (const [id, frame] of frames) if (frame.cur) snapshot[id] = b64encode(frame.cur);
      localStorage.setItem(CACHE_KEY, JSON.stringify({ home: S.home, frames: snapshot, at: Date.now() }));
    } catch (_) { /* storage full or blocked */ }
  }

  function loadCache() {
    try {
      const raw = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
      if (!raw || !raw.home) return false;
      setHome(raw.home, { fromCache: true });
      const t = now();
      for (const [id, text] of Object.entries(raw.frames || {})) {
        const data = b64(text);
        frames.set(Number(id), { prev: data, cur: data, tPrev: t, tCur: t });
      }
      S.cachedAt = raw.at || 0;
      S.frozenFrames = true;
      return true;
    } catch (_) {
      return false;
    }
  }

  function saveView() {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify({ sel: [...S.selection], multi: S.multi, sheet: S.sheet === "closed" ? "closed" : "rest" }));
    } catch (_) { /* ignore */ }
  }

  function loadView() {
    try {
      const view = JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
      if (!view) return;
      S.selection = new Set((view.sel || []).filter((id) => S.lights.has(id)));
      S.multi = Boolean(view.multi) && S.selection.size > 0;
      if (view.sheet !== "closed" && S.selection.size) openSheet("rest", true);
    } catch (_) { /* ignore */ }
  }

  // ----- connection --------------------------------------------------------

  let reconnectDelay = 500;
  let bannerTimer = 0;

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    let socket;
    try {
      socket = new WebSocket(`${scheme}://${location.host}/api/stream`);
    } catch (_) {
      scheduleReconnect();
      return;
    }
    S.socket = socket;
    socket.onopen = () => {
      S.connected = true;
      S.everConnected = true;
      reconnectDelay = 500;
      clearTimeout(bannerTimer);
      setBanner(null);
      sendVisible();
      const queued = S.outbox.splice(0);
      for (const message of queued) socket.send(JSON.stringify(message));
    };
    socket.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      onMessage(message);
    };
    socket.onclose = (event) => {
      S.connected = false;
      S.socket = null;
      if (event.code === 4401) { showLogin(); return; }
      clearTimeout(bannerTimer);
      bannerTimer = setTimeout(() => { if (!S.connected) setBanner("Not connected to the house"); }, 1500);
      scheduleReconnect();
    };
    socket.onerror = () => { try { socket.close(); } catch (_) { /* closed */ } };
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.7, 8000);
  }

  function send(message, { queue = false } = {}) {
    if (S.socket && S.socket.readyState === 1) {
      S.socket.send(JSON.stringify(message));
      return true;
    }
    if (queue) S.outbox.push(message);
    return false;
  }

  function onMessage(message) {
    switch (message.t) {
      case "home":
        setHome(message.home);
        break;
      case "lights":
        for (const view of message.lights || []) mergeLight(view);
        refreshChrome();
        break;
      case "f": {
        const t = now();
        for (const [id, text] of Object.entries(message.f || {})) {
          const key = Number(id);
          const data = b64(text);
          const frame = frames.get(key);
          if (frame && !S.frozenFrames) frames.set(key, { prev: frame.cur, cur: data, tPrev: frame.tCur, tCur: t });
          else frames.set(key, { prev: data, cur: data, tPrev: t - 100, tCur: t });
        }
        S.frozenFrames = false;
        break;
      }
      case "bf":
        boardFrame = { mac: message.mac, data: b64(message.f), t: now() };
        break;
      case "ack":
        if (message.missing && message.missing.length) markMissing(message.missing);
        break;
      case "error":
        toast(message.detail || "That change did not go through");
        break;
      case "drift":
        break;
      case "applied":
        toast("Applied");
        break;
      default:
        break;
    }
  }

  function setBanner(text) {
    const banner = $("#banner");
    banner.hidden = !text;
    banner.textContent = text || "";
  }

  // ----- model -------------------------------------------------------------

  function setHome(home, { fromCache = false } = {}) {
    const layoutBefore = layoutKey();
    S.home = home;
    const seen = new Set();
    for (const view of home.lights || []) {
      mergeLight(view, true);
      seen.add(view.id);
    }
    for (const id of [...S.lights.keys()]) if (!seen.has(id)) S.lights.delete(id);
    for (const id of [...S.selection]) if (!S.lights.has(id)) S.selection.delete(id);
    if (layoutKey() !== layoutBefore || !$("#zones").childElementCount) renderHome();
    else refreshRibbons();
    renderFavorites();
    renderStatus();
    if (S.sheet !== "closed") renderSheet();
    if (!fromCache) saveCache();
  }

  function mergeLight(view, quiet) {
    const current = S.lights.get(view.id);
    const merged = current ? Object.assign(current, view) : { ...view };
    if (!current) S.lights.set(view.id, merged);
    pushBri(view.id, merged.on ? merged.bri : 0);
    if (!quiet) refreshRibbon(view.id);
  }

  function pushBri(id, value) {
    const list = briHistory.get(id) || [];
    const last = list[list.length - 1];
    if (!last || last.v !== value) list.push({ t: now(), v: value });
    while (list.length > 12) list.shift();
    briHistory.set(id, list);
  }

  function briAt(id, t) {
    const list = briHistory.get(id) || [];
    let value = list.length ? list[0].v : 0;
    for (const item of list) if (item.t <= t) value = item.v;
    return value;
  }

  function layoutKey() {
    const lights = [...S.lights.values()].map((l) => `${l.id}:${l.zone_id}:${l.position}:${l.name}`).join("|");
    const zones = (S.home.zones || []).map((z) => `${z.id}:${z.name}:${z.position}:${z.collapsed}`).join("|");
    return lights + "#" + zones;
  }

  function orderedLights(zoneId) {
    return [...S.lights.values()]
      .filter((l) => (l.zone_id || null) === (zoneId || null))
      .sort((a, b) => a.position - b.position || a.id - b.id);
  }

  function zoneName(zoneId) {
    const zone = (S.home.zones || []).find((z) => z.id === zoneId);
    return zone ? zone.name : "";
  }

  function selectionLabel() {
    const ids = [...S.selection];
    if (!ids.length) return "";
    if (ids.length === S.lights.size && ids.length > 1) return "All lights";
    if (ids.length === 1) return S.lights.get(ids[0])?.name || "1 light";
    for (const zone of S.home.zones || []) {
      const members = orderedLights(zone.id).map((l) => l.id);
      if (members.length === ids.length && members.every((id) => S.selection.has(id))) return zone.name;
    }
    return `${ids.length} lights`;
  }

  // ----- Home --------------------------------------------------------------

  function renderStatus() {
    $("#property").textContent = S.home.property || "All lights";
    const attention = S.home.attention || {};
    const count = (attention.offline || 0) + (attention.new || 0);
    const button = $("#attention");
    button.hidden = !count && !attention.firmware;
    button.classList.toggle("up", !count && Boolean(attention.firmware));
    button.textContent = count ? String(count) : "";
  }

  function renderFavorites() {
    const row = $("#favorites");
    const favorites = S.home.favorites || [];
    row.hidden = !favorites.length;
    row.replaceChildren(
      ...favorites.map((fav) => {
        const canvas = el("canvas", { width: 60, height: 60 });
        const first = (fav.body.items || []).find((item) => item.look) || {};
        canvas._look = first.look || null;
        canvas._off = (fav.body.items || []).every((item) => item.on === false);
        const pill = el("button", { class: "fav" }, canvas, fav.name);
        bindPress(pill, {
          tap: () => applyFavorite(fav),
          long: () => favoriteMenu(fav),
        });
        return pill;
      }),
    );
  }

  function ribbonFor(light) {
    let entry = ribbonEls.get(light.id);
    if (entry) return entry;
    const canvas = el("canvas");
    const name = el("span", { class: "name" });
    const pctLabel = el("span", { class: "pct" });
    const root = el("div", { class: "ribbon", "data-id": light.id, role: "button" }, canvas, name, pctLabel);
    entry = { root, canvas, ctx: canvas.getContext("2d"), name, pct: pctLabel, w: 0, h: 0 };
    ribbonEls.set(light.id, entry);
    bindRibbon(entry, light.id);
    observer.observe(root);
    return entry;
  }

  function renderHome() {
    const zones = $("#zones");
    const sections = [];
    const zoneList = [...(S.home.zones || [])].sort((a, b) => a.position - b.position || a.id - b.id);
    for (const zone of zoneList) {
      const lights = orderedLights(zone.id);
      if (!lights.length) continue;
      sections.push(zoneSection(zone, lights));
    }
    const other = orderedLights(null);
    if (other.length) sections.push(zoneSection(null, other));
    if (!sections.length) {
      sections.push(
        el(
          "div",
          { class: "empty" },
          el("strong", { text: S.everConnected || S.connected ? "No lights yet" : "Looking for the house…" }),
          S.everConnected ? "New boards show up in Maintenance. Tap the top line to set one up." : "",
        ),
      );
    }
    zones.replaceChildren(...sections);
    for (const [id, entry] of ribbonEls) {
      if (!S.lights.has(id)) {
        observer.unobserve(entry.root);
        ribbonEls.delete(id);
      }
    }
    refreshRibbons();
    document.body.classList.toggle("selecting", S.selection.size > 0);
  }

  function zoneSection(zone, lights) {
    const rows = el("div", { class: "rows" });
    for (const light of lights) rows.append(ribbonFor(light).root);
    const section = el("section", { class: "zone" + (zone && zone.collapsed ? " collapsed" : ""), "data-zone": zone ? zone.id : "" });
    const name = el("button", { class: "zone-name", text: zone ? zone.name : "Other" });
    if (!zone && !(S.home.zones || []).some((z) => orderedLights(z.id).length)) name.style.visibility = "hidden";
    const bri = el("span", { class: "zone-bri num" });
    const toggle = el("button", { class: "toggle", "aria-label": "Power" });
    const fill = el("div", { class: "zone-fill" });
    const head = el("div", { class: "zone-head" }, name, fill, bri, toggle);
    section.append(head, rows);
    section._update = () => {
      const members = orderedLights(zone ? zone.id : null);
      const lit = members.filter((l) => l.on);
      bri.textContent = lit.length ? `${pct(lit.reduce((sum, l) => sum + l.bri, 0) / lit.length)}%` : "";
      toggle.classList.toggle("on", lit.length > 0);
    };
    section._update();
    name.addEventListener("click", () => {
      if (!zone) return;
      const collapsed = !section.classList.contains("collapsed");
      section.classList.toggle("collapsed", collapsed);
      zone.collapsed = collapsed;
      api(`/api/zones/${zone.id}`, { method: "PATCH", body: { collapsed } }).catch(() => {});
    });
    bindPress(fill, {
      tap: () => selectSet(orderedLights(zone ? zone.id : null).map((l) => l.id), true),
      long: () => zone && zoneMenu(zone),
    });
    bindPress(bri, { tap: () => selectSet(orderedLights(zone ? zone.id : null).map((l) => l.id), true), long: () => zone && zoneMenu(zone) });
    toggle.addEventListener("click", () => {
      const members = orderedLights(zone ? zone.id : null);
      const turnOn = !members.some((l) => l.on);
      haptic("medium");
      commit(members.map((l) => ({ light: l.id, on: turnOn })), {
        label: `${zone ? zone.name : "Other"} ${turnOn ? "on" : "off"}`,
        kind: "power",
      });
    });
    return section;
  }

  function refreshRibbons() {
    for (const id of S.lights.keys()) refreshRibbon(id);
    refreshChrome();
  }

  function refreshRibbon(id) {
    const light = S.lights.get(id);
    const entry = ribbonEls.get(id);
    if (!light || !entry) return;
    if (!entry.editing) entry.name.textContent = light.name;
    if (!entry.root.classList.contains("dragging-bri")) entry.pct.textContent = `${pct(light.bri)}%`;
    entry.root.classList.toggle("off", !light.on);
    entry.root.classList.toggle("offline", !light.online);
    entry.root.classList.toggle("sel", S.selection.has(id));
  }

  function refreshChrome() {
    document.querySelectorAll(".zone").forEach((section) => section._update && section._update());
    document.body.classList.toggle("selecting", S.selection.size > 0);
    for (const [id, entry] of ribbonEls) entry.root.classList.toggle("sel", S.selection.has(id));
    renderHandle();
    if (S.sheet !== "closed") updateDials();
  }

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const id = Number(entry.target.dataset.id);
        if (entry.isIntersecting) visible.add(id);
        else visible.delete(id);
      }
      sendVisible();
    },
    { root: null, rootMargin: "60px" },
  );

  let visTimer = 0;
  function sendVisible() {
    clearTimeout(visTimer);
    visTimer = setTimeout(() => {
      const ids = new Set(visible);
      if (S.sheet !== "closed") for (const id of S.selection) ids.add(id);
      send({ t: "vis", ids: [...ids], board: S.screen && S.screen.kind === "find" ? S.screen.mac : null });
    }, 60);
  }

  // ----- drawing -----------------------------------------------------------

  const scratch = new Map();

  function buffer(key, size) {
    let buf = scratch.get(key);
    if (!buf || buf.length !== size) {
      buf = new Float32Array(size);
      scratch.set(key, buf);
    }
    return buf;
  }

  // Pixels the ribbon should show right now, 0..255 floats, plus the point count.
  function pixelsFor(light, t, target) {
    const frame = frames.get(light.id);
    const points = frame ? frame.cur.length / 3 : lightCount(light.len);
    const out = target && target.length === points * 3 ? target : buffer(`p${light.id}`, points * 3);
    const shown = light.on ? light.bri : 0;
    if (frame && frame.cur.length) {
      const span = clamp(frame.tCur - frame.tPrev, 60, 260);
      let k = clamp((t - frame.tCur) / span, 0, 1);
      const prev = frame.prev && frame.prev.length === frame.cur.length ? frame.prev : frame.cur;
      let delta = 0;
      for (let i = 0; i < frame.cur.length; i += 9) delta += Math.abs(frame.cur[i] - prev[i]);
      if (delta / (frame.cur.length / 9) > 80) k = 1; // strobe-like: cut, do not smear
      // Frames from the board lag what we sent; scale them to the brightness we show now.
      const frameBri = briAt(light.id, t - 260);
      let scale = 1;
      if (frameBri > 0) scale = clamp(shown / frameBri, 0, 4);
      else if (shown > 0) scale = -1;
      if (scale < 0) {
        Fx.render(lightLook(light), points, t / 1000, out);
        const f = shown / 255;
        for (let i = 0; i < out.length; i++) out[i] *= f;
      } else {
        for (let i = 0; i < out.length; i++) out[i] = (prev[i] + (frame.cur[i] - prev[i]) * k) * scale;
      }
    } else {
      Fx.render(lightLook(light), points, t / 1000, out);
      const f = shown / 255;
      for (let i = 0; i < out.length; i++) out[i] *= f;
    }
    const pred = predictions.get(light.id);
    if (pred) applyPrediction(light, pred, points, t, out);
    return out;
  }

  function applyPrediction(light, pred, points, t, out) {
    if (t > pred.until) {
      predictions.delete(light.id);
      return;
    }
    const predicted = buffer(`q${light.id}`, points * 3);
    Fx.render(pred.look || lightLook(light), points, t / 1000, predicted);
    const level = (pred.on === false ? 0 : pred.bri == null ? (light.on ? light.bri : 0) : pred.bri) / 255;
    for (let i = 0; i < predicted.length; i++) predicted[i] *= level;
    const from = pred.from && pred.from.length === predicted.length ? pred.from : null;
    const elapsed = t - pred.t0;
    const handOff = pred.until - 220;
    if (elapsed < pred.dur && from) {
      const p = elapsed / pred.dur;
      const eased = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      for (let i = 0; i < points; i++) {
        const x = pred.dir < 0 ? 1 - (i + 0.5) / points : (i + 0.5) / points;
        let w;
        if (pred.kind === "sweep") w = clamp((eased * 1.15 - x) / 0.15, 0, 1);
        else w = eased;
        for (let c = 0; c < 3; c++) out[i * 3 + c] = from[i * 3 + c] + (predicted[i * 3 + c] - from[i * 3 + c]) * w;
      }
      return;
    }
    if (t < handOff) {
      out.set(predicted);
      return;
    }
    const w = clamp((t - handOff) / 220, 0, 1);
    for (let i = 0; i < out.length; i++) out[i] = predicted[i] + (out[i] - predicted[i]) * w;
  }

  function predict(ids, fields, { kind = "sweep", dur = SWEEP_MS, dir = 1, hold = 700 } = {}) {
    const t = now();
    for (const id of ids) {
      const light = S.lights.get(id);
      if (!light) continue;
      const frame = frames.get(id);
      const points = frame ? frame.cur.length / 3 : lightCount(light.len);
      const snapshot = new Float32Array(points * 3);
      pixelsFor(light, t, snapshot);
      predictions.set(id, {
        look: fields.look || (predictions.get(id) || {}).look || lightLook(light),
        bri: fields.bri,
        on: fields.on,
        from: snapshot,
        t0: t,
        dur,
        until: t + dur + hold,
        kind,
        dir,
      });
    }
  }

  function sizeCanvas(entry, canvas) {
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width * DPR));
    const h = Math.max(1, Math.round(rect.height * DPR));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    return [w, h];
  }

  function drawStrip(ctx, w, h, px, t, { desaturate = 0, radius = 0, falloff = true, normalize = false } = {}) {
    const points = px.length / 3;
    let max = 0;
    for (let i = 0; i < px.length; i++) if (px[i] > max) max = px[i];
    ctx.clearRect(0, 0, w, h);
    if (max < 5 && !normalize) {
      ctx.fillStyle = "#141416";
      ctx.fillRect(0, 0, w, h);
      return [20, 20, 22];
    }
    const lift = normalize && max > 0 ? 255 / max : 1;
    const drift = t / 1000;
    const color = (i) => {
      let r = px[i * 3] * lift;
      let g = px[i * 3 + 1] * lift;
      let b = px[i * 3 + 2] * lift;
      const n = 1 + 0.03 * Math.sin(drift * 0.7 + i * 0.9) * Math.sin(drift * 0.31 + i * 0.37);
      r *= n; g *= n; b *= n;
      if (desaturate) {
        const y = 0.3 * r + 0.59 * g + 0.11 * b;
        r += (y - r) * desaturate; g += (y - g) * desaturate; b += (y - b) * desaturate;
      }
      return [clamp(r, 0, 255) | 0, clamp(g, 0, 255) | 0, clamp(b, 0, 255) | 0];
    };
    let sr = 0, sg = 0, sb = 0, sw = 0;
    if (points < 20) {
      ctx.fillStyle = "#0e0e10";
      ctx.fillRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";
      const step = w / points;
      for (let i = 0; i < points; i++) {
        const [r, g, b] = color(i);
        const cx = (i + 0.5) * step;
        const grad = ctx.createRadialGradient(cx, h / 2, 0, cx, h / 2, step * 0.95);
        grad.addColorStop(0, `rgba(${r},${g},${b},1)`);
        grad.addColorStop(0.55, `rgba(${r},${g},${b},0.55)`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad;
        ctx.fillRect(cx - step, 0, step * 2, h);
        const weight = r + g + b;
        sr += r * weight; sg += g * weight; sb += b * weight; sw += weight;
      }
      ctx.globalCompositeOperation = "source-over";
    } else {
      const grad = ctx.createLinearGradient(0, 0, w, 0);
      for (let i = 0; i < points; i++) {
        const [r, g, b] = color(i);
        grad.addColorStop((i + 0.5) / points, `rgb(${r},${g},${b})`);
        const weight = r + g + b;
        sr += r * weight; sg += g * weight; sb += b * weight; sw += weight;
      }
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }
    if (falloff) {
      const shade = ctx.createLinearGradient(0, 0, 0, h);
      shade.addColorStop(0, "rgba(0,0,0,0.30)");
      shade.addColorStop(0.45, "rgba(0,0,0,0)");
      shade.addColorStop(0.6, "rgba(0,0,0,0)");
      shade.addColorStop(1, "rgba(0,0,0,0.42)");
      ctx.fillStyle = shade;
      ctx.fillRect(0, 0, w, h);
    }
    if (!sw) return [255, 255, 255];
    return [sr / sw, sg / sw, sb / sw];
  }

  function glowColor(rgb) {
    const max = Math.max(rgb[0], rgb[1], rgb[2], 1);
    const f = 255 / max;
    return `rgb(${(rgb[0] * f) | 0},${(rgb[1] * f) | 0},${(rgb[2] * f) | 0})`;
  }

  function frameLoop() {
    const t = now();
    const staleCache = S.frozenFrames && Date.now() - S.cachedAt > 10 * 60 * 1000;
    for (const id of visible) {
      const light = S.lights.get(id);
      const entry = ribbonEls.get(id);
      if (!light || !entry || entry.root.classList.contains("lifted-src")) continue;
      const [w, h] = sizeCanvas(entry, entry.canvas);
      const px = pixelsFor(light, t);
      const desat = !light.online || staleCache || (!S.connected && S.everConnected) ? 0.6 : 0;
      const dominant = drawStrip(entry.ctx, w, h, px, t, { desaturate: desat });
      if (!entry.glowAt || t - entry.glowAt > 300) {
        entry.glowAt = t;
        entry.root.style.setProperty("--glow", glowColor(dominant));
      }
    }
    drawSheetCanvases(t);
    drawFavorites(t);
    if (S.screen && S.screen.kind === "find") drawFind(t);
    requestAnimationFrame(frameLoop);
  }

  function drawMiniLight(canvas, light, t, normalize) {
    const ctx = canvas._ctx || (canvas._ctx = canvas.getContext("2d"));
    const [w, h] = sizeCanvas(null, canvas);
    drawStrip(ctx, w, h, pixelsFor(light, t), t, { falloff: false, normalize });
  }

  const swatchCanvases = new Map();
  function swatchCanvas(w, h) {
    const key = `${w}x${h}`;
    if (!swatchCanvases.has(key)) {
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      swatchCanvases.set(key, { canvas, ctx: canvas.getContext("2d") });
    }
    return swatchCanvases.get(key);
  }

  // A swatch is a small field of the Look's light: a few rows of the effect, each a
  // moment apart, blended so it reads as glow rather than as a striped chart.
  function drawLookSwatch(canvas, look, t, { points = 24, square = true } = {}) {
    if (!canvas.isConnected) return;
    const ctx = canvas._ctx || (canvas._ctx = canvas.getContext("2d"));
    const [w, h] = sizeCanvas(null, canvas);
    const px = buffer(`s${points}`, points * 3);
    if (!square) {
      Fx.render(look, points, t / 1000, px);
      drawStrip(ctx, w, h, px, t, { falloff: false, normalize: true });
      return;
    }
    // Render a tiny rows x points image and let the browser's smoothing scale it up into glow.
    const rows = 6;
    const tiny = swatchCanvas(points, rows);
    const image = tiny.ctx.createImageData(points, rows);
    for (let r = 0; r < rows; r++) {
      Fx.render(look, points, t / 1000 + r * 0.29, px);
      let max = 1;
      for (let i = 0; i < px.length; i++) if (px[i] > max) max = px[i];
      const lift = Math.min(3, 235 / max) * (0.78 + 0.22 * Math.sin((r / (rows - 1)) * Math.PI));
      for (let i = 0; i < points; i++) {
        const o = (r * points + ((i + (r % 2) * 3) % points)) * 4;
        image.data[o] = Math.min(255, px[i * 3] * lift);
        image.data[o + 1] = Math.min(255, px[i * 3 + 1] * lift);
        image.data[o + 2] = Math.min(255, px[i * 3 + 2] * lift);
        image.data[o + 3] = 255;
      }
    }
    tiny.ctx.putImageData(image, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(tiny.canvas, 0, 0, w, h);
    ctx.globalAlpha = 1;
    const vignette = ctx.createRadialGradient(w / 2, h / 2, w * 0.25, w / 2, h / 2, w * 0.78);
    vignette.addColorStop(0, "rgba(0,0,0,0)");
    vignette.addColorStop(1, "rgba(0,0,0,0.5)");
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, w, h);
  }

  function drawFavorites(t) {
    for (const canvas of document.querySelectorAll(".fav canvas")) {
      if (canvas._off || !canvas._look) {
        if (canvas._drawnOff) continue;
        const ctx = canvas.getContext("2d");
        const w = canvas.width;
        ctx.clearRect(0, 0, w, w);
        ctx.fillStyle = "#141416";
        ctx.beginPath();
        ctx.arc(w / 2, w / 2, w / 2 - 1, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "rgba(232,232,234,0.35)";
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(w / 2, w / 2, w / 4, -Math.PI * 0.3, Math.PI * 1.3);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(w / 2, w / 2 - w / 3.2);
        ctx.lineTo(w / 2, w / 2 - w / 12);
        ctx.stroke();
        canvas._drawnOff = true;
        continue;
      }
      drawLookSwatch(canvas, canvas._look, t, { points: 12 });
    }
  }

  // ----- gestures ----------------------------------------------------------

  function bindPress(node, { tap, long, double }) {
    let timer = 0;
    let start = null;
    let fired = false;
    let lastTap = 0;
    let tapTimer = 0;
    node.addEventListener("pointerdown", (event) => {
      if (event.button) return;
      start = { x: event.clientX, y: event.clientY };
      fired = false;
      clearTimeout(timer);
      if (long) timer = setTimeout(() => { fired = true; haptic("light"); long(event); }, 480);
    });
    node.addEventListener("pointermove", (event) => {
      if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) clearTimeout(timer);
    });
    const end = (event) => {
      clearTimeout(timer);
      if (!start || fired || event.type !== "pointerup") { start = null; return; }
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) { start = null; return; }
      start = null;
      if (double) {
        const t = Date.now();
        if (t - lastTap < 260) { clearTimeout(tapTimer); lastTap = 0; double(event); return; }
        lastTap = t;
        tapTimer = setTimeout(() => tap && tap(event), 230);
      } else if (tap) tap(event);
    };
    node.addEventListener("pointerup", end);
    node.addEventListener("pointercancel", end);
    node.addEventListener("contextmenu", (event) => event.preventDefault());
  }

  function bindRibbon(entry, id) {
    const root = entry.root;
    let g = null;

    root.addEventListener("pointerdown", (event) => {
      if (event.button || entry.editing) return;
      const light = S.lights.get(id);
      if (!light) return;
      g = {
        x: event.clientX,
        y: event.clientY,
        t: Date.now(),
        mode: null,
        startBri: light.on ? light.bri : 0,
        width: root.getBoundingClientRect().width,
        onName: event.target === entry.name,
        pointer: event.pointerId,
        lastSent: 0,
        long: false,
      };
      g.timer = setTimeout(() => {
        if (!g || g.mode) return;
        g.long = true;
        if (g.onName) {
          g.mode = "done";
          startRename(entry, id);
          return;
        }
        haptic("light");
        toggleMulti(id);
        g.mode = "long";
      }, 480);
    });

    root.addEventListener("pointermove", (event) => {
      if (!g || g.pointer !== event.pointerId) return;
      const dx = event.clientX - g.x;
      const dy = event.clientY - g.y;
      if (!g.mode) {
        if (Math.abs(dx) > 12 && Math.abs(dy) < 6) {
          g.mode = "bri";
          clearTimeout(g.timer);
          root.setPointerCapture(event.pointerId);
          root.classList.add("dragging-bri");
        } else if (Math.abs(dy) > 8 || Math.abs(dx) > 24) {
          clearTimeout(g.timer);
          g.mode = "scroll";
        }
      } else if (g.mode === "long" && Math.abs(dy) > 10) {
        // Long-press then move: this is a reorder, not a selection.
        toggleMulti(id);
        g.mode = "drag";
        root.setPointerCapture(event.pointerId);
        startReorder(entry, id, event);
      }
      if (g.mode === "bri") briDrag(id, entry, g, dx);
      if (g.mode === "drag") moveReorder(event);
    });

    const finish = (event) => {
      if (!g || g.pointer !== event.pointerId) return;
      clearTimeout(g.timer);
      const dx = event.clientX - g.x;
      const elapsed = Date.now() - g.t;
      const mode = g.mode;
      const state = g;
      g = null;
      if (mode === "bri") {
        root.classList.remove("dragging-bri");
        if (event.type === "pointerup" && elapsed < 260 && dx > 28 && state.startBri >= 0) {
          // A quick right flick is a power toggle, not a brightness change.
          revertBri(id, state);
          powerSwipe(entry, id);
          return;
        }
        endBri(id, state);
        return;
      }
      if (mode === "drag") { endReorder(event); return; }
      if (mode || event.type !== "pointerup") return;
      if (Math.hypot(dx, event.clientY - state.y) > 10) return;
      ribbonTap(id);
    };
    root.addEventListener("pointerup", finish);
    root.addEventListener("pointercancel", finish);
    root.addEventListener("contextmenu", (event) => event.preventDefault());
    // Once a gesture is ours, the page must not scroll under it.
    root.addEventListener(
      "touchmove",
      (event) => { if (g && (g.mode === "bri" || g.mode === "drag" || g.mode === "long")) event.preventDefault(); },
      { passive: false },
    );
  }

  let tapState = { id: null, at: 0, timer: 0 };
  function ribbonTap(id) {
    const t = Date.now();
    if (tapState.id === id && t - tapState.at < 260) {
      clearTimeout(tapState.timer);
      tapState = { id: null, at: 0, timer: 0 };
      identify(id);
      return;
    }
    clearTimeout(tapState.timer);
    tapState = {
      id,
      at: t,
      timer: setTimeout(() => {
        tapState = { id: null, at: 0, timer: 0 };
        if (S.multi) toggleMulti(id);
        else selectSet([id], true);
      }, 230),
    };
  }

  function identify(id) {
    const light = S.lights.get(id);
    if (!light) return;
    haptic("light");
    api(`/api/lights/${id}/identify`, { method: "POST" }).catch((error) => toast(error.message));
    const entry = ribbonEls.get(id);
    if (!entry) return;
    // Mirror the three white flashes on the ribbon so you know which one you hit.
    const t0 = now();
    predictions.set(id, {
      look: { fx: "Solid", col: [[255, 255, 255], [0, 0, 0], [0, 0, 0]] },
      bri: 255,
      from: null,
      t0,
      dur: 0,
      until: t0 + 1200,
      kind: "flash",
      dir: 1,
    });
    let step = 0;
    const tick = () => {
      step += 1;
      const p = predictions.get(id);
      if (!p || p.kind !== "flash") return;
      p.bri = step % 2 ? 0 : 255;
      if (step < 6) setTimeout(tick, 200);
      else predictions.delete(id);
    };
    setTimeout(tick, 200);
  }

  function briDrag(id, entry, g, dx) {
    const light = S.lights.get(id);
    if (!light) return;
    const target = Math.round(clamp(g.startBri + (dx / g.width) * 255 * 1.25, 0, 255));
    const wasOn = light.on;
    if (target <= 0) {
      if (wasOn) haptic("strong");
      light.on = false;
    } else {
      if (!wasOn || (target === 255 && light.bri !== 255)) haptic("medium");
      light.on = true;
      light.bri = target;
    }
    entry.pct.textContent = `${pct(target)}%`;
    entry.root.classList.toggle("off", !light.on);
    pushBri(id, light.on ? light.bri : 0);
    if (!g.undo) g.undo = { light: id, on: g.startBri > 0, bri: g.startBri > 0 ? g.startBri : light.bri };
    const t = Date.now();
    if (t - g.lastSent >= 100) {
      g.lastSent = t;
      send({ t: "apply", seq: ++S.seq, preview: true, tt: 0, changes: [light.on ? { light: id, bri: target, on: true } : { light: id, on: false }] });
    }
    refreshChrome();
  }

  function endBri(id, g) {
    const light = S.lights.get(id);
    if (!light) return;
    const change = light.on ? { light: id, bri: light.bri, on: true } : { light: id, on: false };
    commit([change], {
      label: light.on ? `${light.name} → ${pct(light.bri)}%` : `${light.name} off`,
      kind: "bri",
      undo: g.undo ? [g.undo] : null,
      tt: 0,
    });
  }

  function revertBri(id, g) {
    const light = S.lights.get(id);
    if (!light) return;
    light.on = g.startBri > 0;
    if (g.startBri > 0) light.bri = g.startBri;
    pushBri(id, light.on ? light.bri : 0);
    refreshRibbon(id);
  }

  function powerSwipe(entry, id) {
    const light = S.lights.get(id);
    if (!light) return;
    entry.root.classList.add("power-swipe");
    setTimeout(() => entry.root.classList.remove("power-swipe"), 180);
    haptic("medium");
    commit([{ light: id, on: !light.on }], { label: `${light.name} ${light.on ? "off" : "on"}`, kind: "power" });
  }

  function startRename(entry, id) {
    const light = S.lights.get(id);
    if (!light) return;
    haptic("light");
    entry.editing = true;
    const input = el("input", { value: light.name, maxlength: 40, enterkeyhint: "done", "aria-label": "Light name" });
    entry.name.replaceChildren(input);
    input.focus();
    input.select();
    const done = (save) => {
      if (!entry.editing) return;
      entry.editing = false;
      const value = input.value.trim();
      entry.name.textContent = save && value ? value : light.name;
      if (save && value && value !== light.name) {
        light.name = value;
        api(`/api/lights/${id}`, { method: "PATCH", body: { name: value } }).catch((error) => toast(error.message));
      }
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); input.blur(); }
      if (event.key === "Escape") done(false);
    });
    input.addEventListener("blur", () => done(true));
    input.addEventListener("pointerdown", (event) => event.stopPropagation());
  }

  // Reorder and drag-to-merge -------------------------------------------------

  let drag = null;

  function startReorder(entry, id, event) {
    const rect = entry.root.getBoundingClientRect();
    const placeholder = el("div", { class: "placeholder" });
    entry.root.after(placeholder);
    entry.root.classList.add("lifted");
    entry.root.style.left = `${rect.left}px`;
    entry.root.style.width = `${rect.width}px`;
    entry.root.style.top = `${rect.top}px`;
    document.body.append(entry.root);
    haptic("medium");
    drag = { id, entry, placeholder, offset: event.clientY - rect.top, merge: null, mergeTimer: 0, hover: null };
  }

  function moveReorder(event) {
    if (!drag) return;
    const top = event.clientY - drag.offset;
    drag.entry.root.style.top = `${top}px`;
    const home = $("#home");
    const homeRect = home.getBoundingClientRect();
    if (event.clientY < homeRect.top + 60) home.scrollTop -= 8;
    if (event.clientY > homeRect.bottom - 80) home.scrollTop += 8;
    const rows = [...document.querySelectorAll("#zones .ribbon")];
    const centerY = event.clientY;
    let hover = null;
    for (const row of rows) {
      const rect = row.getBoundingClientRect();
      if (centerY >= rect.top && centerY <= rect.bottom) {
        const local = (centerY - rect.top) / rect.height;
        hover = { row, middle: local > 0.25 && local < 0.75, before: local < 0.5 };
        break;
      }
    }
    if (hover && hover.middle) {
      if (!drag.hover || drag.hover.row !== hover.row) {
        clearTimeout(drag.mergeTimer);
        if (drag.merge) drag.merge.classList.remove("merge-target");
        drag.merge = null;
        drag.mergeTimer = setTimeout(() => {
          drag.merge = hover.row;
          hover.row.classList.add("merge-target");
          haptic("light");
        }, 380);
      }
    } else {
      clearTimeout(drag.mergeTimer);
      if (drag.merge) drag.merge.classList.remove("merge-target");
      drag.merge = null;
      if (hover) {
        if (hover.before) hover.row.before(drag.placeholder);
        else hover.row.after(drag.placeholder);
      } else {
        // Over a zone header or gap: drop at the end of the nearest section.
        const sections = [...document.querySelectorAll("#zones .zone")];
        for (const section of sections) {
          const rect = section.getBoundingClientRect();
          if (centerY >= rect.top && centerY <= rect.bottom) {
            const rowsEl = section.querySelector(".rows");
            if (centerY < rect.top + 36) rowsEl.prepend(drag.placeholder);
            break;
          }
        }
      }
    }
    drag.hover = hover;
  }

  function endReorder() {
    if (!drag) return;
    const { entry, placeholder, id, merge } = drag;
    clearTimeout(drag.mergeTimer);
    drag = null;
    entry.root.classList.remove("lifted");
    entry.root.style.cssText = "";
    placeholder.replaceWith(entry.root);
    if (merge) {
      merge.classList.remove("merge-target");
      const targetId = Number(merge.dataset.id);
      const target = S.lights.get(targetId);
      const light = S.lights.get(id);
      haptic("success");
      if (target && target.zone_id) {
        light.zone_id = target.zone_id;
        saveLayout();
      } else if (target && light) {
        api("/api/zones", { method: "POST", body: { name: suggestZoneName(target, light), light_ids: [targetId, id] } }).catch((error) => toast(error.message));
      }
      return;
    }
    saveLayout();
  }

  function suggestZoneName(a, b) {
    const words = (name) => name.split(/\s+/);
    const first = words(a.name)[0];
    return first && words(b.name)[0] === first ? first : "New zone";
  }

  function saveLayout() {
    const lights = [];
    const zones = [];
    for (const section of document.querySelectorAll("#zones .zone")) {
      const zoneId = section.dataset.zone ? Number(section.dataset.zone) : null;
      if (zoneId) zones.push(zoneId);
      for (const row of section.querySelectorAll(".ribbon")) {
        const lightId = Number(row.dataset.id);
        lights.push({ id: lightId, zone_id: zoneId });
        const light = S.lights.get(lightId);
        if (light) {
          light.zone_id = zoneId;
          light.position = lights.length;
        }
      }
    }
    api("/api/layout", { method: "PUT", body: { zones, lights } }).catch((error) => toast(error.message));
    renderHome();
  }

  // Two-finger swipe down anywhere undoes the last change.
  (function bindUndoGesture() {
    let start = null;
    document.addEventListener("touchstart", (event) => {
      start = event.touches.length === 2 ? [...event.touches].map((touch) => touch.clientY) : null;
    }, { passive: true });
    document.addEventListener("touchend", (event) => {
      if (!start || event.touches.length) return;
      const ends = [...event.changedTouches].map((touch) => touch.clientY);
      if (ends.length && ends.every((y, i) => y - (start[i] ?? start[0]) > 60)) undo();
      start = null;
    }, { passive: true });
  })();

  // ----- selection ---------------------------------------------------------

  function selectSet(ids, open) {
    S.selection = new Set(ids);
    S.multi = false;
    haptic("light");
    refreshChrome();
    saveView();
    if (open && ids.length) openSheet("rest");
  }

  function toggleMulti(id) {
    if (S.selection.has(id)) S.selection.delete(id);
    else S.selection.add(id);
    S.multi = S.selection.size > 0;
    refreshChrome();
    saveView();
  }

  function clearSelection() {
    S.selection.clear();
    S.multi = false;
    refreshChrome();
    saveView();
  }

  function renderHandle() {
    const handle = $("#handle");
    const show = S.selection.size > 0 && S.sheet === "closed" && !S.screen;
    handle.classList.toggle("up", show);
    $("#handle-label").textContent = selectionLabel();
    const swatches = $("#handle-swatches");
    const ids = [...S.selection].slice(0, 3);
    if (swatches.dataset.ids !== ids.join(",")) {
      swatches.dataset.ids = ids.join(",");
      swatches.replaceChildren(...ids.map((id) => { const c = el("canvas"); c._light = id; return c; }));
    }
  }

  (function bindHandle() {
    const handle = $("#handle");
    let start = null;
    handle.addEventListener("pointerdown", (event) => {
      start = { y: event.clientY, t: Date.now(), onLabel: Boolean(event.target.closest("#handle-label")) };
      handle.setPointerCapture(event.pointerId);
      clearTimeout(handle._long);
      handle._long = setTimeout(() => { start = null; haptic("light"); saveFavoriteFlow(); }, 520);
    });
    handle.addEventListener("pointermove", (event) => {
      if (start && Math.abs(event.clientY - start.y) > 8) clearTimeout(handle._long);
      if (start && start.y - event.clientY > 30) { start = null; openSheet("rest"); }
    });
    handle.addEventListener("pointerup", (event) => {
      clearTimeout(handle._long);
      if (!start) return;
      const moved = Math.abs(event.clientY - start.y);
      const onLabel = start.onLabel;
      start = null;
      if (moved > 8) return;
      if (onLabel && S.selection.size > 1) {
        selectionMenu();
        return;
      }
      openSheet("rest");
    });
  })();

  function selectionMenu() {
    const label = selectionLabel();
    const isZone = (S.home.zones || []).some((zone) => zone.name === label);
    const items = [{ label: "Paint", strong: true, action: () => openSheet("rest") }];
    if (!isZone) {
      items.push({
        label: "Save as Zone",
        action: async () => {
          const name = await ask("Name this zone", "", "Front of house");
          if (name) api("/api/zones", { method: "POST", body: { name, light_ids: [...S.selection] } }).catch((error) => toast(error.message));
        },
      });
    }
    items.push({ label: "Save as Favorite", action: saveFavoriteFlow });
    items.push({ label: "Clear selection", action: clearSelection });
    menu({ title: label, items });
  }

  // ----- Paint sheet -------------------------------------------------------

  const sheet = $("#sheet");
  let sheetDrag = null;
  let trayReady = false;

  function sheetOffset(state) {
    const h = sheet.getBoundingClientRect().height || window.innerHeight * 0.9;
    if (state === "full") return 0;
    if (state === "rest") return Math.max(0, h - window.innerHeight * 0.6);
    return h + 30;
  }

  function openSheet(state, instant) {
    if (!S.selection.size) return;
    const wasClosed = S.sheet === "closed";
    S.sheet = state;
    document.body.classList.add("painting");
    sheet.classList.toggle("full", state === "full");
    sheet.setAttribute("aria-hidden", "false");
    if (instant) sheet.classList.add("dragging");
    sheet.classList.remove("closing");
    sheet.style.transform = `translateY(${sheetOffset(state)}px)`;
    if (instant) requestAnimationFrame(() => sheet.classList.remove("dragging"));
    if (wasClosed) {
      renderSheet();
      haptic("light");
    }
    renderHandle();
    sendVisible();
    saveView();
  }

  function closeSheet() {
    S.sheet = "closed";
    document.body.classList.remove("painting");
    sheet.classList.add("closing");
    sheet.style.transform = `translateY(${sheetOffset("closed")}px)`;
    sheet.setAttribute("aria-hidden", "true");
    setBandMode(false);
    renderHandle();
    sendVisible();
    saveView();
  }

  $("#scrim").addEventListener("click", closeSheet);

  for (const zone of [$("#sheet-grab"), $("#sel-head")]) {
    zone.addEventListener("pointerdown", (event) => {
      if (event.target.closest("#sel-all")) return;
      sheetDrag = { y: event.clientY, base: sheetOffset(S.sheet), t: Date.now(), moved: false };
      zone.setPointerCapture(event.pointerId);
      sheet.classList.add("dragging");
    });
    zone.addEventListener("pointermove", (event) => {
      if (!sheetDrag) return;
      const dy = event.clientY - sheetDrag.y;
      if (Math.abs(dy) > 6) sheetDrag.moved = true;
      sheet.style.transform = `translateY(${Math.max(-8, sheetDrag.base + dy)}px)`;
    });
    zone.addEventListener("pointerup", (event) => {
      if (!sheetDrag) return;
      const dy = event.clientY - sheetDrag.y;
      const velocity = dy / Math.max(1, Date.now() - sheetDrag.t);
      const moved = sheetDrag.moved;
      sheetDrag = null;
      sheet.classList.remove("dragging");
      if (!moved) {
        if (event.target.closest("#sel-stack")) closeSheet();
        else sheet.style.transform = `translateY(${sheetOffset(S.sheet)}px)`;
        return;
      }
      const position = sheetOffset(S.sheet) + dy;
      if (velocity > 0.6 || position > sheetOffset("rest") + 110) closeSheet();
      else if (velocity < -0.4 || position < sheetOffset("rest") / 2) openSheet("full");
      else openSheet("rest");
    });
  }

  $("#sel-all").addEventListener("click", () => {
    S.selection = new Set(S.lights.keys());
    S.multi = false;
    refreshChrome();
    renderSheetHeader();
    saveView();
  });

  function renderSheet() {
    renderSheetHeader();
    renderTray();
    updateDials();
    renderMore();
  }

  function renderSheetHeader() {
    const ids = [...S.selection];
    const stack = el("div", { class: "sel-ribbons" });
    for (const id of ids.slice(0, 3)) {
      const canvas = el("canvas");
      canvas._light = id;
      stack.append(canvas);
    }
    const label = selectionLabel();
    const sub = ids.length > 3 ? `+${ids.length - 3} more` : ids.length > 1 ? `${ids.length} lights` : zoneName(S.lights.get(ids[0])?.zone_id) || "";
    $("#sel-stack").replaceChildren(stack, el("div", { style: "min-width:0" }, el("div", { class: "sel-name", text: label }), el("div", { class: "sel-sub", text: sub })));
    $("#sel-all").hidden = ids.length === S.lights.size;
  }

  // Look tray ------------------------------------------------------------------

  const tray = $("#tray");
  let trayItems = [];
  let centered = -1;
  let trayTouch = false;
  let trayProgrammatic = false;
  let settleTimer = 0;
  let previewTimer = 0;
  let lastPreviewAt = 0;
  let pendingPreview = null;
  let scrubUndo = null;
  let committedKey = "";

  function currentLookId() {
    const ids = [...S.selection];
    const first = S.lights.get(ids[0]);
    if (!first) return null;
    return ids.every((id) => S.lights.get(id)?.look_id === first.look_id) ? first.look_id : null;
  }

  function renderTray() {
    const looks = S.home.looks || [];
    trayItems = [{ id: "solid", name: "Solid", body: { fx: "Solid", col: [[255, 172, 92], [0, 0, 0], [0, 0, 0]] } }, ...looks];
    tray.replaceChildren(
      ...trayItems.map((look, index) => {
        const canvas = el("canvas");
        canvas._look = look.body;
        canvas._solid = look.id === "solid";
        const node = el("button", { class: "swatch", "data-index": index }, canvas, el("span", { text: look.name }));
        node.addEventListener("click", () => {
          if (index === centered) {
            if (look.id === "solid") setBandMode(true);
            else commitLook(look);
            return;
          }
          trayTouch = false;
          scrollToIndex(index, true);
          setTimeout(() => (look.id === "solid" ? setBandMode(true) : commitLook(look)), 260);
        });
        return node;
      }),
    );
    const lookId = currentLookId();
    const first = selected()[0];
    let index = lookId != null ? trayItems.findIndex((item) => item.id === lookId) : -1;
    if (index < 0 && first && String(first.fx).toLowerCase() === "solid") index = 0;
    if (index < 0) index = 0;
    trayReady = false;
    requestAnimationFrame(() => {
      scrollToIndex(index, false);
      centered = index;
      markCentered(index);
      committedKey = keyFor(trayItems[index]);
      trayReady = true;
    });
    updateTrayNote();
  }

  function keyFor(look) {
    return look ? `${look.id}` : "";
  }

  function scrollToIndex(index, smooth) {
    const node = tray.children[index];
    if (!node) return;
    trayProgrammatic = true;
    const left = node.offsetLeft - (tray.clientWidth - node.offsetWidth) / 2;
    tray.scrollTo({ left, behavior: smooth ? "smooth" : "auto" });
    setTimeout(() => { trayProgrammatic = false; }, smooth ? 450 : 60);
  }

  function markCentered(index) {
    for (const node of tray.children) node.classList.toggle("center", Number(node.dataset.index) === index);
  }

  function centerIndex() {
    const middle = tray.scrollLeft + tray.clientWidth / 2;
    let best = 0;
    let bestDistance = Infinity;
    for (const node of tray.children) {
      const distance = Math.abs(node.offsetLeft + node.offsetWidth / 2 - middle);
      if (distance < bestDistance) { bestDistance = distance; best = Number(node.dataset.index); }
    }
    return best;
  }

  tray.addEventListener("pointerdown", () => { trayTouch = true; });
  tray.addEventListener("touchstart", () => { trayTouch = true; }, { passive: true });
  const trayRelease = () => {
    trayTouch = false;
    scheduleSettle();
  };
  tray.addEventListener("pointerup", trayRelease);
  tray.addEventListener("touchend", trayRelease, { passive: true });
  tray.addEventListener("pointercancel", () => { /* native scroll took over; touchend still fires */ });

  tray.addEventListener("scroll", () => {
    if (!trayReady) return;
    const index = centerIndex();
    if (index !== centered) {
      centered = index;
      markCentered(index);
      if (!trayProgrammatic) {
        haptic("light");
        const look = trayItems[index];
        if (look && look.id !== "solid") queuePreview(look);
      }
    }
    if (!trayProgrammatic) scheduleSettle();
  }, { passive: true });

  function scheduleSettle() {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      if (trayTouch || trayProgrammatic) return;
      const look = trayItems[centered];
      if (!look) return;
      if (keyFor(look) === committedKey) {
        if (scrubUndo) restoreAfterScrub();
        return;
      }
      if (look.id === "solid") {
        restoreAfterScrub();
        return;
      }
      commitLook(look);
    }, 160);
  }

  // While scrolling, the centered Look is shown on the real lights, throttled.
  function queuePreview(look) {
    const lights = selected();
    if (!lights.length) return;
    if (!scrubUndo) scrubUndo = lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
    pendingPreview = look;
    predict(lights.map((l) => l.id), { look: look.body }, { kind: "fade", dur: 120, hold: 1500 });
    const gap = lights.length > MANY ? PREVIEW_MANY_MS : PREVIEW_MS;
    const wait = Math.max(0, lastPreviewAt + gap - Date.now());
    clearTimeout(previewTimer);
    previewTimer = setTimeout(flushPreview, wait);
    updateTrayNote(true);
  }

  function flushPreview() {
    const look = pendingPreview;
    pendingPreview = null;
    if (!look) return;
    lastPreviewAt = Date.now();
    const ids = [...S.selection];
    send({ t: "apply", seq: ++S.seq, preview: true, tt: 0, changes: ids.map((id) => ({ light: id, look_id: look.id })) });
  }

  function restoreAfterScrub() {
    // Scrubbed and came back to where we started: put the previous look back.
    const undoState = scrubUndo;
    scrubUndo = null;
    clearTimeout(previewTimer);
    pendingPreview = null;
    updateTrayNote(false);
    if (!undoState) return;
    send({ t: "apply", seq: ++S.seq, preview: false, tt: COMMIT_TT, changes: undoState.map((item) => ({ light: item.light, look: item.look })) }, { queue: true });
    predict(undoState.map((item) => item.light), {}, { kind: "fade", dur: 200 });
    for (const item of undoState) predictions.get(item.light) && (predictions.get(item.light).look = item.look);
  }

  function updateTrayNote(scrubbing) {
    const count = S.selection.size;
    $("#tray-note").textContent = scrubbing && count > MANY ? `previewing ${count} lights` : "";
  }

  function commitLook(look) {
    clearTimeout(previewTimer);
    pendingPreview = null;
    const lights = selected();
    if (!lights.length) return;
    const undoState = scrubUndo || lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
    scrubUndo = null;
    committedKey = keyFor(look);
    updateTrayNote(false);
    for (const light of lights) light.look_id = look.id;
    commit(lights.map((l) => ({ light: l.id, look_id: look.id })), {
      label: `${selectionLabel()} → ${look.name}`,
      kind: "look",
      look: look.body,
      undo: undoState,
    });
  }

  function markMissing(ids) {
    const names = ids.map((id) => S.lights.get(id)?.name).filter(Boolean);
    if (names.length) toast(`${names.join(", ")} used a stand-in effect · Update firmware in Maintenance`);
  }

  // Solid color band ---------------------------------------------------------

  const band = $("#band");
  let bandSlot = 0;
  let bandUndo = null;
  let bandLastSent = 0;

  function setBandMode(on, slot = 0) {
    bandSlot = slot;
    $("#tray-wrap").hidden = on;
    $("#band-wrap").hidden = !on;
    if (on) {
      drawBand();
      const first = selected()[0];
      if (first && first.col && first.col[slot]) placeKnobFor(first.col[slot]);
    }
    updateDials();
  }

  function kelvin(k) {
    const t = k / 100;
    let r, g, b;
    if (t <= 66) { r = 255; g = 99.47 * Math.log(t) - 161.12; b = t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04; }
    else { r = 329.7 * Math.pow(t - 60, -0.1332); g = 288.12 * Math.pow(t - 60, -0.0755); b = 255; }
    return [clamp(r, 0, 255) | 0, clamp(g, 0, 255) | 0, clamp(b, 0, 255) | 0];
  }

  function hsv(h, s) {
    const out = [0, 0, 0];
    const i = Math.floor(h * 6);
    const f = h * 6 - i;
    const p = 1 - s, q = 1 - f * s, t = 1 - (1 - f) * s;
    const table = [[1, t, p], [q, 1, p], [p, 1, t], [p, q, 1], [t, p, 1], [1, p, q]][((i % 6) + 6) % 6];
    for (let c = 0; c < 3; c++) out[c] = Math.round(table[c] * 255);
    return out;
  }

  function bandColor(x) {
    const sat = Number($("#sat").value) / 100;
    if (x < 0.5) {
      const rgb = kelvin(2000 + (x / 0.5) * 4800);
      const y = 255;
      return rgb.map((c) => Math.round(y + (c - y) * sat));
    }
    return hsv((x - 0.5) / 0.5, sat);
  }

  function drawBand() {
    const ctx = band.getContext("2d");
    const [w, h] = sizeCanvas(null, band);
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    for (let i = 0; i <= 48; i++) {
      const x = i / 48;
      const [r, g, b] = bandColor(Math.min(0.999, x));
      grad.addColorStop(x, `rgb(${r},${g},${b})`);
    }
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
    const shade = ctx.createLinearGradient(0, 0, 0, h);
    shade.addColorStop(0, "rgba(0,0,0,0)");
    shade.addColorStop(1, "rgba(0,0,0,0.25)");
    ctx.fillStyle = shade;
    ctx.fillRect(0, 0, w, h);
  }

  function placeKnob(x) {
    const rect = band.getBoundingClientRect();
    $("#band-knob").style.left = `${16 + clamp(x, 0, 1) * rect.width}px`;
  }

  function placeKnobFor(color) {
    let best = 0;
    let bestDistance = Infinity;
    for (let i = 0; i <= 200; i++) {
      const x = Math.min(0.999, i / 200);
      const c = bandColor(x);
      const d = Math.abs(c[0] - color[0]) + Math.abs(c[1] - color[1]) + Math.abs(c[2] - color[2]);
      if (d < bestDistance) { bestDistance = d; best = x; }
    }
    band._x = best;
    placeKnob(best);
  }

  function bandLook(light, color) {
    const col = (light.col || [[0, 0, 0], [0, 0, 0], [0, 0, 0]]).map((c) => [...c]);
    col[bandSlot] = color;
    if (bandSlot === 0) return { fx: "Solid", pal: "Default", col, sx: light.sx, ix: light.ix };
    return { ...lightLook(light), col };
  }

  function bandAt(event, final) {
    const rect = band.getBoundingClientRect();
    const x = clamp((event.clientX - rect.left) / rect.width, 0, 0.999);
    band._x = x;
    placeKnob(x);
    const color = bandColor(x);
    const lights = selected();
    if (!bandUndo) bandUndo = lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
    const changes = lights.map((l) => ({ light: l.id, look: bandLook(l, color) }));
    for (const change of changes) predictions.delete(change.light);
    predict(lights.map((l) => l.id), { look: changes[0].look }, { kind: "fade", dur: 60, hold: 900 });
    for (const change of changes) {
      const p = predictions.get(change.light);
      if (p) p.look = change.look;
    }
    if (final) {
      const undoState = bandUndo;
      bandUndo = null;
      for (const l of lights) Object.assign(l, changes.find((c) => c.light === l.id).look, { look_id: null });
      commit(changes, { label: `${selectionLabel()} → color`, kind: "color", undo: undoState, noPredict: true });
      return;
    }
    if (Date.now() - bandLastSent > 90) {
      bandLastSent = Date.now();
      send({ t: "apply", seq: ++S.seq, preview: true, tt: 0, changes });
    }
  }

  band.addEventListener("pointerdown", (event) => {
    band.setPointerCapture(event.pointerId);
    band._down = true;
    bandAt(event, false);
  });
  band.addEventListener("pointermove", (event) => { if (band._down) bandAt(event, false); });
  band.addEventListener("pointerup", (event) => { if (!band._down) return; band._down = false; bandAt(event, true); });
  band.addEventListener("pointercancel", () => { band._down = false; });
  $("#sat").addEventListener("input", () => { drawBand(); });
  $("#sat").addEventListener("change", () => {
    if (band._x == null) return;
    const rect = band.getBoundingClientRect();
    bandAt({ clientX: rect.left + band._x * rect.width }, true);
  });
  $("#band-back").addEventListener("click", () => setBandMode(false));

  // Dials ----------------------------------------------------------------------

  const dials = {};
  for (const node of document.querySelectorAll(".dial")) {
    const key = node.dataset.dial;
    const track = $(".track", node);
    const shade = key === "bri" ? el("div", { class: "shade" }) : null;
    if (shade) track.insertBefore(shade, $(".thumb", track));
    dials[key] = { node, track, fill: $(".fill", node), thumb: $(".thumb", node), value: $("[data-value]", node), shade, canvas: $(".track-ribbon", node) };
    bindDial(key);
  }

  function dialValue(key) {
    const lights = selected();
    if (!lights.length) return 0;
    if (key === "bri") {
      const lit = lights.filter((l) => l.on);
      return lit.length ? lit.reduce((sum, l) => sum + l.bri, 0) / lit.length : 0;
    }
    return lights.reduce((sum, l) => sum + (l[key] ?? 128), 0) / lights.length;
  }

  function setDialVisual(key, value) {
    const dial = dials[key];
    const x = clamp(value / 255, 0, 1);
    dial.thumb.style.left = `${x * 100}%`;
    if (dial.shade) dial.shade.style.left = `${x * 100}%`;
    else dial.fill.style.width = `${x * 100}%`;
    dial.value.textContent = key === "bri" ? `${pct(value)}%` : `${pct(value)}`;
  }

  function updateDials() {
    if (dials.bri.dragging || dials.sx.dragging || dials.ix.dragging) return;
    for (const key of Object.keys(dials)) setDialVisual(key, dialValue(key));
    const first = selected()[0];
    if (first) {
      for (const node of document.querySelectorAll("#palettes .mini")) node.classList.toggle("on", node.textContent === first.pal);
      for (const node of document.querySelectorAll("#effects .mini")) node.classList.toggle("on", node.textContent === first.fx);
    }
    const solid = selected().every((l) => String(l.fx).toLowerCase() === "solid");
    dials.ix.node.hidden = solid;
    dials.sx.node.hidden = solid;
  }

  function bindDial(key) {
    const dial = dials[key];
    let undoState = null;
    let lastSent = 0;
    let lastValue = 0;
    const at = (event, final) => {
      const rect = dial.track.getBoundingClientRect();
      const value = Math.round(clamp((event.clientX - rect.left) / rect.width, 0, 1) * 255);
      if ((value === 0 || value === 255) && value !== lastValue) haptic("medium");
      lastValue = value;
      setDialVisual(key, value);
      const lights = selected();
      if (!undoState) undoState = lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
      let changes;
      if (key === "bri") {
        changes = lights.map((l) => (value > 0 ? { light: l.id, bri: value, on: true } : { light: l.id, on: false }));
        for (const l of lights) {
          l.on = value > 0;
          if (value > 0) l.bri = value;
          pushBri(l.id, l.on ? l.bri : 0);
          refreshRibbon(l.id);
        }
      } else {
        changes = lights.map((l) => ({ light: l.id, look: { ...lightLook(l), [key]: value } }));
        for (const l of lights) l[key] = value;
      }
      if (final) {
        const saved = undoState;
        undoState = null;
        const what = key === "bri" ? `${pct(value)}%` : `${key === "sx" ? "speed" : "intensity"} ${pct(value)}`;
        commit(changes, { label: `${selectionLabel()} → ${what}`, kind: key, undo: saved, tt: key === "bri" ? 0 : 2, noPredict: true });
        return;
      }
      if (Date.now() - lastSent > 100) {
        lastSent = Date.now();
        send({ t: "apply", seq: ++S.seq, preview: true, tt: 0, changes });
      }
      refreshChrome();
    };
    dial.track.addEventListener("pointerdown", (event) => {
      dial.track.setPointerCapture(event.pointerId);
      dial.dragging = true;
      at(event, false);
    });
    dial.track.addEventListener("pointermove", (event) => { if (dial.dragging) at(event, false); });
    dial.track.addEventListener("pointerup", (event) => {
      if (!dial.dragging) return;
      dial.dragging = false;
      at(event, true);
    });
    dial.track.addEventListener("pointercancel", () => { dial.dragging = false; undoState = null; });
  }

  // More: palette, colors, effects, save ------------------------------------

  function renderMore() {
    const first = selected()[0];
    if (!first) return;
    const palettes = (S.home.palettes || []).filter((name) => name && !name.startsWith("~"));
    $("#palettes").replaceChildren(
      ...palettes.map((name) => {
        const canvas = el("canvas");
        canvas._palette = name;
        const node = el("button", { class: "mini" + (first.pal === name ? " on" : "") }, canvas, el("span", { text: name }));
        node.addEventListener("click", () => applyField("pal", name, `palette ${name}`));
        return node;
      }),
    );
    $("#dots").replaceChildren(
      ...[0, 1, 2].map((slot) => {
        const c = (first.col || [])[slot] || [0, 0, 0];
        const dot = el("button", { class: "dot", style: `background: rgb(${c[0]},${c[1]},${c[2]})`, "aria-label": `Color ${slot + 1}` });
        dot.addEventListener("click", () => { openSheet("rest"); setBandMode(true, slot); });
        return dot;
      }),
    );
    const effects = [...new Set((S.home.effects || []).filter((name) => name && name !== "RSVD" && name !== "-"))].sort((a, b) => a.localeCompare(b));
    $("#effects").replaceChildren(
      ...effects.map((name) => {
        const canvas = el("canvas");
        canvas._effect = name;
        const node = el("button", { class: "mini" + (first.fx === name ? " on" : "") }, canvas, el("span", { text: name }));
        node.addEventListener("click", () => applyField("fx", name, name));
        return node;
      }),
    );
    moreObserver.disconnect();
    for (const canvas of document.querySelectorAll(".more canvas")) moreObserver.observe(canvas);
  }

  const moreVisible = new Set();
  const moreObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) moreVisible.add(entry.target);
      else moreVisible.delete(entry.target);
    }
  });

  function applyField(field, value, what) {
    const lights = selected();
    const undoState = lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
    const changes = lights.map((l) => ({ light: l.id, look: { ...lightLook(l), [field]: value } }));
    for (const l of lights) { l[field] = value; l.look_id = null; }
    commit(changes, { label: `${selectionLabel()} → ${what}`, kind: "look", look: changes[0].look, undo: undoState });
    renderMore();
  }

  $("#save-look").addEventListener("click", async () => {
    const first = selected()[0];
    if (!first) return;
    const name = await ask("Save as Look", "", "Porch warm");
    if (!name) return;
    try {
      const look = await api("/api/looks", { method: "POST", body: { name, body: lightLook(first) } });
      for (const l of selected()) l.look_id = look.id;
      haptic("success");
      toast(`Saved ${name}`);
    } catch (error) { toast(error.message); }
  });

  $("#save-fav").addEventListener("click", saveFavoriteFlow);

  async function saveFavoriteFlow() {
    const lights = selected();
    if (!lights.length) return;
    const name = await ask("Save as Favorite", "Saves these lights with their look and brightness.", "Movie night");
    if (!name) return;
    const items = lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
    try {
      await api("/api/favorites", { method: "POST", body: { name, items } });
      haptic("success");
      toast(`Saved ${name}`);
    } catch (error) { toast(error.message); }
  }

  function drawSheetCanvases(t) {
    for (const canvas of document.querySelectorAll("#sel-stack canvas, #handle-swatches canvas")) {
      const light = S.lights.get(canvas._light);
      if (light && canvas.isConnected && canvas.offsetParent) drawMiniLight(canvas, light, t, false);
    }
    if (S.sheet === "closed") return;
    const first = selected()[0];
    if (first && dials.bri.canvas) drawMiniLight(dials.bri.canvas, first, t, true);
    if (!$("#tray-wrap").hidden) {
      const center = centered;
      for (const node of tray.children) {
        const index = Number(node.dataset.index);
        if (Math.abs(index - center) > 3) continue;
        const canvas = node.firstChild;
        if (canvas._solid) drawSolidSwatch(canvas);
        else drawLookSwatch(canvas, canvas._look, t, { points: 18 });
      }
    }
    if (S.sheet === "full") {
      for (const canvas of moreVisible) {
        if (canvas._palette) {
          drawLookSwatch(canvas, { fx: "Palette", pal: canvas._palette, col: first ? first.col : undefined, sx: 60 }, t, { points: 24, square: false });
        } else if (canvas._effect) {
          drawLookSwatch(canvas, { ...(first ? lightLook(first) : {}), fx: canvas._effect }, t, { points: 24, square: false });
        }
      }
    }
  }

  function drawSolidSwatch(canvas) {
    if (canvas._drawn) return;
    const ctx = canvas.getContext("2d");
    const [w, h] = sizeCanvas(null, canvas);
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    for (let i = 0; i <= 12; i++) {
      const [r, g, b] = bandColor(Math.min(0.999, i / 12));
      grad.addColorStop(i / 12, `rgb(${r},${g},${b})`);
    }
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
    canvas._drawn = true;
  }

  // ----- committing changes and undo -------------------------------------------

  let lastUndo = null;
  let pillTimer = 0;

  // Optimistic: the screen changes now, the message goes out now, the server reconciles.
  function commit(changes, { label, kind, look, undo: undoState, tt, noPredict, reverse } = {}) {
    if (!changes.length) return;
    const before = undoState || changes.map((c) => {
      const l = S.lights.get(c.light);
      return l ? { light: l.id, on: l.on, bri: l.bri, look: lightLook(l) } : null;
    }).filter(Boolean);
    for (const change of changes) {
      const light = S.lights.get(change.light);
      if (!light) continue;
      if (!noPredict && (change.look_id != null || change.look)) {
        const body = change.look || (S.home.looks || []).find((item) => item.id === change.look_id)?.body;
        predict([light.id], { look: body }, { kind: "sweep", dir: reverse ? -1 : 1 });
        if (body) Object.assign(light, { fx: body.fx, pal: body.pal || "Default", col: body.col || light.col, sx: body.sx ?? light.sx, ix: body.ix ?? light.ix });
      }
      if (change.on != null) light.on = change.on;
      if (change.bri != null && change.bri > 0) { light.bri = change.bri; light.on = change.on !== false; }
      if ((change.look_id != null || change.look) && change.on == null) light.on = true;
      pushBri(light.id, light.on ? light.bri : 0);
      refreshRibbon(light.id);
    }
    refreshChrome();
    const message = { t: "apply", seq: ++S.seq, preview: false, changes };
    if (tt != null) message.tt = tt;
    else if (kind === "power") message.tt = changes.some((c) => c.on === false) ? 5 : 3;
    else message.tt = COMMIT_TT;
    if (!send(message, { queue: true })) toast("Will apply when the house is back");
    if (!reverse) {
      lastUndo = { changes: before, label };
      showPill(label);
    }
  }

  function undo() {
    if (!lastUndo) return;
    const { changes } = lastUndo;
    lastUndo = null;
    hidePill();
    haptic("medium");
    commit(changes.map((item) => ({ light: item.light, on: item.on, bri: item.on ? item.bri : undefined, look: item.look })), { reverse: true, kind: "undo" });
    if (S.sheet !== "closed") { renderTray(); updateDials(); }
  }

  function showPill(label) {
    const pill = $("#pill");
    pill.replaceChildren(el("span", null, label || "Changed", " · "), el("b", { text: "Undo" }));
    pill.onclick = undo;
    pill.classList.add("show");
    clearTimeout(pillTimer);
    pillTimer = setTimeout(hidePill, 4000);
  }

  function toast(text) {
    const pill = $("#pill");
    pill.replaceChildren(el("span", { text }));
    pill.onclick = hidePill;
    pill.classList.add("show");
    clearTimeout(pillTimer);
    pillTimer = setTimeout(hidePill, 3000);
  }

  function hidePill() {
    $("#pill").classList.remove("show");
  }

  async function applyFavorite(fav) {
    const items = (fav.body.items || []).filter((item) => S.lights.has(item.light));
    if (!items.length) return;
    haptic("light");
    const before = items.map((item) => {
      const l = S.lights.get(item.light);
      return { light: l.id, on: l.on, bri: l.bri, look: lightLook(l) };
    });
    commit(items.map((item) => ({ light: item.light, on: item.on, bri: item.bri, look: item.look })), { label: `${fav.name}`, kind: "favorite", undo: before });
  }

  function favoriteMenu(fav) {
    menu({
      title: fav.name,
      items: [
        { label: "Apply", strong: true, action: () => applyFavorite(fav) },
        {
          label: "Delete",
          danger: true,
          action: () => api(`/api/favorites/${fav.id}`, { method: "DELETE" }).catch((error) => toast(error.message)),
        },
      ],
    });
  }

  function zoneMenu(zone) {
    menu({
      title: zone.name,
      items: [
        {
          label: "Rename",
          action: async () => {
            const name = await ask("Rename zone", "", zone.name, zone.name);
            if (name) api(`/api/zones/${zone.id}`, { method: "PATCH", body: { name } }).catch((error) => toast(error.message));
          },
        },
        {
          label: "Convert to Favorite",
          action: async () => {
            const lights = orderedLights(zone.id);
            const name = await ask("Save as Favorite", "", zone.name, zone.name);
            if (!name) return;
            const items = lights.map((l) => ({ light: l.id, on: l.on, bri: l.bri, look: lightLook(l) }));
            api("/api/favorites", { method: "POST", body: { name, items } }).then(() => { haptic("success"); toast(`Saved ${name}`); }).catch((error) => toast(error.message));
          },
        },
        { label: "Delete zone", danger: true, action: () => api(`/api/zones/${zone.id}`, { method: "DELETE" }).catch((error) => toast(error.message)) },
      ],
    });
  }

  // ----- menus -------------------------------------------------------------------

  function menu({ title, items, input }) {
    return new Promise((resolve) => {
      const layer = $("#menu");
      const card = el("div", { class: "menu-card" });
      if (title) card.append(el("div", { class: "menu-title", text: title }));
      let field = null;
      if (input) {
        field = el("input", { class: "menu-input", value: input.value || "", placeholder: input.placeholder || "", maxlength: 40, enterkeyhint: "done" });
        card.append(field);
      }
      const close = (value) => {
        layer.hidden = true;
        layer.replaceChildren();
        resolve(value);
      };
      for (const item of items) {
        card.append(
          el("button", {
            class: (item.danger ? "danger " : "") + (item.strong ? "strong" : ""),
            text: item.label,
            onclick: () => {
              const value = field ? field.value.trim() : true;
              close(value);
              if (item.action) item.action(value);
            },
          }),
        );
      }
      const cancel = el("button", { class: "menu-cancel", text: "Cancel", onclick: () => close(null) });
      layer.replaceChildren(card, cancel);
      layer.hidden = false;
      layer.onclick = (event) => { if (event.target === layer) close(null); };
      if (field) {
        setTimeout(() => field.focus(), 50);
        field.addEventListener("keydown", (event) => {
          if (event.key === "Enter") { event.preventDefault(); close(field.value.trim()); }
        });
      }
    });
  }

  async function ask(title, _note, placeholder, value) {
    const result = await menu({ title, input: { placeholder, value }, items: [{ label: "Save", strong: true }] });
    return typeof result === "string" && result ? result : null;
  }

  function confirmAction(title, label) {
    return menu({ title, items: [{ label, danger: true }] }).then((value) => value === true);
  }

  // ----- pushed screens ----------------------------------------------------------

  const screen = $("#screen");

  function pushScreen(state, build) {
    if (S.sheet !== "closed") closeSheet();
    S.screen = state;
    screen.hidden = false;
    screen.replaceChildren(...build());
    requestAnimationFrame(() => screen.classList.add("in"));
    renderHandle();
    sendVisible();
  }

  function popScreen() {
    const previous = S.screen;
    if (previous && previous.kind === "find" && previous.held) api(`/api/boards/${previous.mac}/release`, { method: "POST" }).catch(() => {});
    if (previous && previous.back) {
      previous.back();
      return;
    }
    S.screen = null;
    screen.classList.remove("in");
    setTimeout(() => { if (!S.screen) { screen.hidden = true; screen.replaceChildren(); } }, 330);
    renderHandle();
    sendVisible();
  }

  // Swipe from the left edge goes back.
  (function bindEdgeSwipe() {
    let start = null;
    screen.addEventListener("touchstart", (event) => {
      const touch = event.touches[0];
      start = touch.clientX < 24 ? { x: touch.clientX, y: touch.clientY } : null;
    }, { passive: true });
    screen.addEventListener("touchend", (event) => {
      if (!start) return;
      const touch = event.changedTouches[0];
      if (touch.clientX - start.x > 70 && Math.abs(touch.clientY - start.y) < 60) popScreen();
      start = null;
    }, { passive: true });
  })();

  function bar(label = "Lights") {
    return el("header", { class: "bar" }, el("button", { class: "back", text: label, onclick: popScreen }));
  }

  // Maintenance -------------------------------------------------------------------

  $("#status").addEventListener("click", () => openMaintenance());
  bindPress($("#property"), { long: () => openMaintenance("boards") });

  async function openMaintenance(focus) {
    pushScreen({ kind: "maint" }, () => [bar(), el("h1", { text: "Maintenance" }), el("p", { class: "sub", text: "Loading…" })]);
    await renderMaintenance(focus);
  }

  async function renderMaintenance(focus) {
    let boards, devices, status, bins, settings;
    try {
      [boards, devices, status, bins, settings] = await Promise.all([
        api("/api/boards"),
        api("/api/devices"),
        api("/api/status"),
        api("/api/firmware/bins"),
        api("/api/settings"),
      ]);
    } catch (error) {
      screen.replaceChildren(bar(), el("h1", { text: "Maintenance" }), el("p", { class: "sub", text: error.message }));
      return;
    }
    if (!S.screen || S.screen.kind !== "maint") return;
    const byMac = new Map(devices.map((d) => [d.mac, d]));
    const pending = (await api("/api/home").catch(() => ({ attention: {} }))).attention || {};
    const parts = [bar(), el("h1", { text: "Maintenance" })];

    const offline = boards.filter((b) => b.status === "adopted" && !b.online);
    const fresh = boards.filter((b) => b.status === "new" && b.online);
    const attention = [];
    for (const board of offline) {
      attention.push(
        el("div", { class: "item" },
          el("span", { class: "led" }),
          el("div", { class: "grow" }, el("div", { class: "title", text: board.name }), el("div", { class: "meta", text: `Offline · last seen ${ago(board.last_seen)}` })),
          el("button", { class: "act", text: "Retry", onclick: () => api("/api/scan", { method: "POST" }).then(() => toast("Looking for it…")) }),
        ),
      );
    }
    for (const board of fresh) {
      attention.push(
        el("div", { class: "item" },
          el("span", { class: "led on" }),
          el("div", { class: "grow" }, el("div", { class: "title", text: `New board · ${board.name}` }), el("div", { class: "meta", text: `${board.ip} · ${board.outputs.length || 1} output${board.outputs.length === 1 ? "" : "s"}` })),
          el("button", { class: "act attn", text: "Set up", onclick: () => openFind(board.mac) }),
        ),
      );
    }
    const firmware = pending.firmware ? await firmwareRows() : [];
    attention.push(...firmware);
    if (attention.length) {
      parts.push(el("h2", { class: "amber", text: "Needs attention" }), el("div", { class: "list" }, ...attention));
    }

    parts.push(el("h2", { text: "Boards", id: "boards" }));
    parts.push(
      el("div", { class: "list" },
        ...boards.map((board) => {
          const device = byMac.get(board.mac) || {};
          const outputs = board.outputs.length || 1;
          const more = el("button", { class: "act", text: "···", "aria-label": `More for ${board.name}` });
          more.addEventListener("click", (event) => { event.stopPropagation(); boardMenu(board, device); });
          return el("button", { class: "item", onclick: () => openFind(board.mac) },
            el("span", { class: "led" + (board.online ? " on" : "") }),
            el("div", { class: "grow" },
              el("div", { class: "title", text: board.name }),
              el("div", { class: "meta num", text: `${board.ip} · ${(board.arch || "").toUpperCase()} · ${board.version || "?"} · ${outputs} output${outputs === 1 ? "" : "s"}` }),
            ),
            more,
          );
        }),
        boards.length ? null : el("div", { class: "item" }, el("div", { class: "meta", text: "No boards found yet. Rescan below, or add an address under Network." })),
      ),
    );

    parts.push(el("h2", { text: "Firmware files" }));
    const upload = el("input", { type: "file", accept: ".bin", hidden: true });
    upload.addEventListener("change", async () => {
      const file = upload.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      try {
        await api("/api/firmware/bins", { method: "POST", body: form });
        toast(`Added ${file.name}`);
        renderMaintenance();
      } catch (error) { toast(error.message); }
    });
    parts.push(
      el("div", { class: "list" },
        ...bins.map((bin) =>
          el("div", { class: "item" },
            el("div", { class: "grow" }, el("div", { class: "title", text: bin.filename }), el("div", { class: "meta", text: `${bin.chip_family || "unknown chip"} · ${(bin.byte_size / 1024).toFixed(0)} KB` })),
          ),
        ),
        el("button", { class: "item", onclick: () => upload.click() }, el("div", { class: "grow" }, el("div", { class: "title", text: "Upload a .bin" }))),
        upload,
      ),
    );

    parts.push(el("h2", { text: "Server" }));
    const property = el("input", { value: S.home.property || "", placeholder: "Home name", maxlength: 40 });
    const extra = el("input", { value: settings.extra_hosts || "", placeholder: "Extra hosts, e.g. 192.168.1.40" });
    const subnets = el("input", { value: settings.scan_subnets || "", placeholder: "Subnets to sweep, e.g. 192.168.1.0/24" });
    parts.push(
      el("div", { class: "field" }, property, el("button", { class: "btn", text: "Save", onclick: () => api("/api/property", { method: "PUT", body: { name: property.value } }).then(() => toast("Saved")).catch((e) => toast(e.message)) })),
      el("p", { class: "note", text: "Shown at the top of Home." }),
      el("div", { class: "field", style: "margin-top:14px" }, extra),
      el("div", { class: "field", style: "margin-top:8px" }, subnets, el("button", { class: "btn", text: "Save", onclick: () => api("/api/settings", { method: "PUT", body: { extra_hosts: extra.value, scan_subnets: subnets.value } }).then(() => toast("Saved")).catch((e) => toast(e.message)) })),
      el("div", { class: "list", style: "margin-top:14px" },
        el("div", { class: "item" }, el("div", { class: "grow" }, el("div", { class: "title", text: `Server ${status.version}` }), el("div", { class: "meta", text: `Last scan ${ago(status.scan && status.scan.at)} · ${(status.scan && status.scan.online) || 0} online` }))),
        el("button", { class: "item", onclick: () => api("/api/scan", { method: "POST" }).then(() => toast("Scanning the network")) }, el("div", { class: "grow" }, el("div", { class: "title", text: "Rescan now" }))),
        status.auth ? el("button", { class: "item", onclick: () => api("/api/logout", { method: "POST" }).then(() => location.reload()) }, el("div", { class: "grow" }, el("div", { class: "title", text: "Sign out" }))) : null,
      ),
    );
    screen.replaceChildren(...parts);
    if (focus === "boards") $("#boards", screen)?.scrollIntoView();
  }

  async function firmwareRows() {
    const rows = [];
    const list = await api("/api/firmware/pending").catch(() => []);
    for (const item of list) {
      const progress = el("div", { class: "progress", hidden: true }, el("div", { style: "width:0%" }));
      rows.push(
        el("div", { class: "item" },
          el("div", { class: "grow" }, el("div", { class: "title", text: item.name }), el("div", { class: "meta num", text: `Firmware ${item.from} → ${item.to}` }), progress),
          el("button", { class: "act attn", text: "Update", onclick: (event) => runFirmware([item], event.currentTarget) }),
        ),
      );
    }
    if (list.length > 1) {
      rows.push(el("button", { class: "item", onclick: (event) => runFirmware(list, event.currentTarget) }, el("div", { class: "grow" }, el("div", { class: "title", text: "Update all, one at a time" }))));
    }
    return rows;
  }

  async function runFirmware(items, button) {
    button.disabled = true;
    for (const item of items) {
      try {
        const job = await api("/api/bulk/firmware", { method: "POST", body: { macs: [item.mac], bin_id: item.bin_id } });
        await waitJob(job.id, (state) => { button.textContent = state.status === "running" ? `Updating ${item.name}…` : state.status; });
      } catch (error) {
        toast(error.message);
        break;
      }
    }
    button.disabled = false;
    renderMaintenance();
  }

  async function waitJob(id, onTick) {
    for (let i = 0; i < 400; i++) {
      const job = await api(`/api/jobs/${id}`);
      onTick && onTick(job);
      if (job.status === "done" || job.status === "error") {
        if (job.status === "error") throw new Error(job.error || "The update failed");
        const failed = (job.results || []).find((row) => row.ok === false);
        if (failed) throw new Error(failed.error || "The update failed");
        return job;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error("Timed out");
  }

  function ago(iso) {
    if (!iso) return "never";
    const seconds = (Date.now() - Date.parse(iso)) / 1000;
    if (seconds < 90) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
  }

  function boardMenu(board, device) {
    menu({
      title: `${board.name} · ${board.ip}`,
      items: [
        {
          label: "Rename board",
          action: async () => {
            const name = await ask("Rename board", "", board.name, board.name);
            if (!name) return;
            api(`/api/devices/${board.mac}/rename`, { method: "POST", body: { name } }).then(() => { toast("Renamed"); renderMaintenance(); }).catch((e) => toast(e.message));
          },
        },
        { label: "Backups", action: () => openBackups(board) },
        { label: "Back up now", action: () => api(`/api/devices/${board.mac}/backup`, { method: "POST" }).then(() => toast("Backed up")).catch((e) => toast(e.message)) },
        { label: "Update firmware…", action: () => chooseFirmware(board) },
        {
          label: "Reboot",
          action: async () => {
            if (await confirmAction(`Reboot ${board.name}? Its lights go dark for a few seconds.`, "Reboot")) {
              api(`/api/devices/${board.mac}/reboot`, { method: "POST" }).then(() => toast("Rebooting")).catch((e) => toast(e.message));
            }
          },
        },
        {
          label: "Forget board",
          danger: true,
          action: async () => {
            if (await confirmAction(`Forget ${board.name}? Its lights leave Home until it is set up again.`, "Forget")) {
              api(`/api/devices/${board.mac}/forget`, { method: "POST", body: { on: true } }).then(() => renderMaintenance()).catch((e) => toast(e.message));
            }
          },
        },
      ].filter(Boolean),
    });
    void device;
  }

  async function chooseFirmware(board) {
    try {
      const [report] = await api("/api/firmware/check", { method: "POST", body: { macs: [board.mac] } });
      const options = (report.bins || []).filter((bin) => bin.ok);
      if (!options.length) {
        toast("No compatible firmware file. Upload one under Firmware files.");
        return;
      }
      menu({
        title: `Firmware for ${board.name} (now ${board.version})`,
        items: options.map((bin) => ({
          label: bin.filename,
          action: async () => {
            if (!(await confirmAction(`Flash ${bin.filename}? A backup runs first.`, "Flash"))) return;
            try {
              const job = await api("/api/bulk/firmware", { method: "POST", body: { macs: [board.mac], bin_id: bin.id } });
              toast("Updating…");
              await waitJob(job.id);
              toast(`${board.name} updated`);
            } catch (error) { toast(error.message); }
          },
        })),
      });
    } catch (error) { toast(error.message); }
  }

  async function openBackups(board) {
    const backTo = S.screen;
    S.screen = { kind: "backups", mac: board.mac, back: () => { S.screen = backTo; renderMaintenance(); } };
    screen.replaceChildren(bar("Maintenance"), el("h1", { text: board.name }), el("p", { class: "sub", text: "Loading backups…" }));
    try {
      const rows = await api(`/api/devices/${board.mac}/backups`);
      screen.replaceChildren(
        bar("Maintenance"),
        el("h1", { text: board.name }),
        el("p", { class: "sub", text: "Saved automatically when found, nightly, when its config changes, and before firmware updates." }),
        el("h2", { text: "Backups" }),
        el("div", { class: "list" },
          ...rows.map((row) =>
            el("button", { class: "item", onclick: () => backupMenu(board, row) },
              el("div", { class: "grow" },
                el("div", { class: "title num", text: new Date(row.created_at).toLocaleString() }),
                el("div", { class: "meta", text: `${row.reason} · firmware ${row.version || "?"}` }),
              ),
              el("span", { class: "chev" }),
            ),
          ),
          rows.length ? null : el("div", { class: "item" }, el("div", { class: "meta", text: "No backups yet." })),
        ),
      );
    } catch (error) { toast(error.message); }
  }

  function backupMenu(board, row) {
    menu({
      title: new Date(row.created_at).toLocaleString(),
      items: [
        {
          label: "Restore this backup",
          danger: true,
          action: async () => {
            haptic("warning");
            if (!(await confirmAction(`Restore ${board.name} to this backup? Its current config is saved first, then it reboots.`, "Restore"))) return;
            try {
              const job = await api(`/api/backups/${row.id}/restore`, { method: "POST" });
              toast("Restoring…");
              await waitJob(job.id);
              toast("Restored");
            } catch (error) { toast(error.message); }
          },
        },
        { label: "Download", action: () => { location.href = `/api/backups/${row.id}/download`; } },
      ],
    });
  }

  // Find ----------------------------------------------------------------------------

  async function openFind(mac) {
    const backTo = S.screen && S.screen.kind === "maint" ? S.screen : null;
    const state = { kind: "find", mac, held: false, picks: {}, back: backTo ? () => { releaseFind(); S.screen = backTo; renderMaintenance(); sendVisible(); } : null };
    if (S.screen) {
      S.screen = state;
      screen.replaceChildren(bar(backTo ? "Maintenance" : "Lights"), el("p", { class: "sub", text: "Loading…" }));
    } else {
      pushScreen(state, () => [bar(), el("p", { class: "sub", text: "Loading…" })]);
    }
    sendVisible();
    await renderFind();
  }

  function releaseFind() {
    const state = S.screen;
    if (state && state.kind === "find" && state.held) {
      state.held = false;
      api(`/api/boards/${state.mac}/release`, { method: "POST" }).catch(() => {});
    }
  }

  async function renderFind() {
    const state = S.screen;
    if (!state || state.kind !== "find") return;
    let boards;
    try { boards = await api("/api/boards"); } catch (error) { toast(error.message); return; }
    const board = boards.find((b) => b.mac === state.mac);
    if (!board) { toast("That board is gone"); popScreen(); return; }
    state.board = board;
    state.total = Math.max(1, ...board.outputs.map((o) => o.stop), board.led_count || 1);
    const flash = el("button", { class: "btn big" + (state.flashing ? " strobing" : ""), text: state.flashing ? "Stop flashing" : "Flash this board" });
    flash.addEventListener("click", async () => {
      haptic("medium");
      try {
        if (state.flashing) {
          state.flashing = false;
          state.held = false;
          await api(`/api/boards/${board.mac}/release`, { method: "POST" });
        } else {
          state.flashing = true;
          state.held = true;
          await api(`/api/boards/${board.mac}/flash`, { method: "POST" });
        }
      } catch (error) { toast(error.message); }
      renderFind();
    });
    const parts = [
      bar(state.back ? "Maintenance" : "Lights"),
      el("h1", { text: board.name }),
      el("p", { class: "sub num", text: `${board.ip} · ${(board.arch || "").toUpperCase()} · WLED ${board.version || "?"}` }),
      flash,
    ];
    const zones = S.home.zones || [];
    board.outputs.forEach((output, index) => {
      const len = Math.max(1, output.stop - output.start);
      const pick = state.picks[output.seg] || { ticks: [] };
      state.picks[output.seg] = pick;
      const canvas = el("canvas");
      canvas._output = output;
      const ruler = el("div", { class: "ruler" });
      // Ticks every 25 LEDs (labels at 50, major at 100); finer on short runs so they can still be counted.
      const step = rulerStep(len);
      const shade = el("div", { class: "span-shade", hidden: true });
      ruler.append(shade);
      for (let led = 0; led <= len; led += step) {
        const label = led % (step * 2) === 0;
        const tick = el("button", {
          class: "tick" + (label ? " label" : "") + (led % (step * 4) === 0 && led ? " major" : "") + (pick.ticks.includes(led) ? " chosen" : ""),
          style: `left:${(led / len) * 100}%`,
          "aria-label": `LED ${led}`,
        }, label ? el("span", { text: String(led) }) : null);
        tick.addEventListener("click", () => pickTick(output, led, len));
        ruler.append(tick);
      }
      const span = spanFor(pick, len);
      if (span) {
        shade.hidden = false;
        shade.style.left = `${(span[0] / len) * 100}%`;
        shade.style.width = `${((span[1] - span[0]) / len) * 100}%`;
      }
      const count = el("div", { class: "count num" }, span ? `${span[1] - span[0]}` : `${len}`, el("small", { text: span ? `LEDs ${span[0]}–${span[1]}` : "LEDs in this output" }));
      const name = el("input", { class: "input", value: output.name || "", placeholder: "Name this light", maxlength: 40 });
      output._name = name;
      output._zone = output.zone_id;
      const chips = el("div", { class: "zone-chips" });
      const drawChips = () => {
        chips.replaceChildren(
          ...zones.map((zone) => {
            const chip = el("button", { class: "chip" + (output._zone === zone.id ? " on" : ""), text: zone.name });
            chip.addEventListener("click", () => { output._zone = output._zone === zone.id ? null : zone.id; drawChips(); });
            return chip;
          }),
        );
      };
      drawChips();
      parts.push(
        el("div", { class: "output" },
          el("div", { class: "output-head" }, el("span", { class: "title", text: `Output ${index + 1}` }), el("span", { class: "meta num", text: `LEDs ${output.start}–${output.stop}` })),
          el("div", { class: "find-ribbon" }, canvas),
          ruler,
          count,
          el("div", { class: "actions" },
            el("button", { class: "btn", text: "Light only this output", onclick: () => solo(output) }),
            span ? el("button", { class: "btn", text: `Set length to ${span[1]}`, onclick: () => setLength(output, span[1]) }) : null,
            state.held ? el("button", { class: "btn", text: "Back to normal", onclick: () => restoreBoard() }) : null,
          ),
          el("div", { class: "name-field" }, name),
          zones.length ? chips : null,
        ),
      );
    });
    parts.push(el("button", { class: "btn big solid", text: board.status === "adopted" ? "Save names" : "Add to Home", onclick: () => saveSetup(board) }));
    screen.replaceChildren(...parts);
  }

  function rulerStep(len) {
    return len >= 100 ? 25 : len >= 40 ? 10 : 5;
  }

  function spanFor(pick, len) {
    if (!pick.ticks.length) return null;
    const step = rulerStep(len);
    if (pick.ticks.length === 1) return [pick.ticks[0], Math.min(len, pick.ticks[0] + step)];
    const [a, b] = [...pick.ticks].sort((x, y) => x - y);
    return a === b ? [a, Math.min(len, a + step)] : [a, b];
  }

  async function pickTick(output, led, len) {
    const state = S.screen;
    const pick = state.picks[output.seg];
    if (pick.ticks.length >= 2) pick.ticks = [];
    pick.ticks.push(led);
    haptic("light");
    const span = spanFor(pick, len);
    state.flashing = false;
    try {
      await api(`/api/boards/${state.mac}/span`, { method: "POST", body: { seg: output.seg, start: span[0], stop: span[1] } });
      state.held = true;
    } catch (error) { toast(error.message); }
    renderFind();
  }

  async function solo(output) {
    const state = S.screen;
    state.flashing = false;
    try {
      await api(`/api/boards/${state.mac}/solo`, { method: "POST", body: { seg: output.seg } });
      state.held = true;
      haptic("medium");
    } catch (error) { toast(error.message); }
    renderFind();
  }

  async function restoreBoard() {
    const state = S.screen;
    state.flashing = false;
    state.held = false;
    for (const key of Object.keys(state.picks)) state.picks[key].ticks = [];
    try { await api(`/api/boards/${state.mac}/release`, { method: "POST" }); } catch (error) { toast(error.message); }
    renderFind();
  }

  async function setLength(output, count) {
    const state = S.screen;
    if (!(await confirmAction(`Make output ${output.seg + 1} exactly ${count} LEDs long?`, "Set length"))) return;
    try {
      await api(`/api/boards/${state.mac}/length`, { method: "POST", body: { seg: output.seg, count } });
      state.picks[output.seg].ticks = [];
      toast(`Output is now ${count} LEDs`);
    } catch (error) { toast(error.message); }
    renderFind();
  }

  async function saveSetup(board) {
    const outputs = board.outputs.map((output) => ({ seg: output.seg, name: output._name.value.trim(), zone_id: output._zone || null }));
    if (!outputs.some((o) => o.name)) {
      toast("Name at least one output");
      return;
    }
    try {
      releaseFind();
      await api(`/api/boards/${board.mac}/setup`, { method: "POST", body: { outputs } });
      haptic("success");
      toast(board.status === "adopted" ? "Saved" : "Added to Home");
      S.screen = null;
      screen.classList.remove("in");
      setTimeout(() => { if (!S.screen) { screen.hidden = true; screen.replaceChildren(); } }, 330);
      renderHandle();
      sendVisible();
    } catch (error) { toast(error.message); }
  }

  function drawFind(t) {
    const state = S.screen;
    if (!boardFrame || boardFrame.mac !== state.mac || !state.total) return;
    const data = boardFrame.data;
    const points = data.length / 3;
    for (const canvas of screen.querySelectorAll(".find-ribbon canvas")) {
      const output = canvas._output;
      if (!output) continue;
      const a = Math.floor((output.start / state.total) * points);
      const b = Math.max(a + 1, Math.ceil((output.stop / state.total) * points));
      const slice = buffer(`f${output.seg}`, (b - a) * 3);
      for (let i = 0; i < slice.length; i++) slice[i] = data[a * 3 + i] || 0;
      const ctx = canvas._ctx || (canvas._ctx = canvas.getContext("2d"));
      const [w, h] = sizeCanvas(null, canvas);
      drawStrip(ctx, w, h, slice, t, { falloff: true });
    }
  }

  // ----- login -------------------------------------------------------------------

  function showLogin() {
    if (S.screen && S.screen.kind === "login") return;
    const input = el("input", { class: "input", type: "password", placeholder: "Password", autocomplete: "current-password" });
    const go = async () => {
      try {
        await api("/api/login", { method: "POST", body: { password: input.value } });
        location.reload();
      } catch (error) { toast(error.message); }
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") go(); });
    S.screen = { kind: "login" };
    screen.hidden = false;
    screen.classList.add("in");
    screen.replaceChildren(el("div", { class: "login" }, el("h1", { text: "Lumen" }), input, el("button", { class: "btn solid", text: "Sign in", onclick: go })));
    setTimeout(() => input.focus(), 50);
  }

  // ----- boot ----------------------------------------------------------------------

  window.addEventListener("resize", () => {
    if (S.sheet !== "closed") sheet.style.transform = `translateY(${sheetOffset(S.sheet)}px)`;
    else sheet.style.transform = `translateY(${sheetOffset("closed")}px)`;
    if (!$("#band-wrap").hidden) drawBand();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveCache();
    else if (!S.connected) connect();
  });
  window.addEventListener("pagehide", saveCache);
  setInterval(saveCache, 15000);

  window.Lumen = { S, frames, commit, undo, selectSet, openSheet, closeSheet, openMaintenance, openFind };

  sheet.style.transform = `translateY(${sheetOffset("closed")}px)`;
  loadCache();
  renderHome();
  renderStatus();
  renderFavorites();
  loadView();
  requestAnimationFrame(frameLoop);
  connect();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
})();
