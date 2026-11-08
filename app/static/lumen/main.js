import { dominant, renderEffect } from "./effects.js";
import { drawRibbon, frameAt } from "./ribbon.js";

const ENGAGE_X = 12;
const CANCEL_Y = 6;
const FLICK_MS = 220;
const FLICK_MIN = 24;
const FLICK_MAX = 96;
const LONG_MS = 480;
const PREVIEW_MS = 150;
const PREVIEW_MANY_MS = 400;
const MANY = 8;
const COMMIT_MS = 400;
const STALE_MS = 10 * 60 * 1000;

const app = document.getElementById("app");
const state = {
  home: null,
  maint: null,
  frames: {},
  screen: "home",
  findMac: null,
  selection: new Set(),
  multi: false,
  sheet: false,
  sheetRatio: 0.6,
  collapsed: new Set(),
  undo: null,
  banner: "",
  connected: true,
  scrubbing: false,
  solid: false,
  draft: null,
  override: {},
  flow: {},
  identify: {},
  ruler: {},
  finder: null,
  zonePick: {},
  confirm: null,
  dragging: false,
  signature: "",
};

window.LUMEN = { engageX: ENGAGE_X, cancelY: CANCEL_Y, previewMs: PREVIEW_MS, previewManyMs: PREVIEW_MANY_MS };
window.__screenViolations = 0;

const gesture = {};
let lastPreview = 0;
let lastScrubMove = 0;
let trayPointer = false;
let saveTimer = 0;

function haptic(kind) {
  if (typeof navigator.vibrate !== "function") return;
  const pattern = { light: 10, medium: 22, success: [12, 28, 14], warning: [24, 40, 24] }[kind] || 10;
  try {
    navigator.vibrate(pattern);
  } catch (error) {
    /* iOS Safari does not implement vibrate. */
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pct(bri) {
  return Math.round((Math.max(0, Math.min(255, bri ?? 0)) / 255) * 100);
}

function lights() {
  return state.home?.lights || [];
}

function pending() {
  return state.home?.pending || [];
}

function allLights() {
  return lights().concat(pending());
}

function lightById(id) {
  return allLights().find((light) => light.id === Number(id));
}

function looks() {
  return state.home?.looks || [];
}

function lookById(id) {
  return looks().find((look) => look.id === Number(id));
}

function selectedLights() {
  return [...state.selection].map(lightById).filter(Boolean);
}

function selectionLabel() {
  const chosen = selectedLights();
  if (!chosen.length) return "";
  if (chosen.length === 1) return chosen[0].name;
  const zones = state.home?.zones || [];
  for (const zone of zones) {
    const members = lights().filter((light) => light.zone_id === zone.id);
    if (members.length === chosen.length && members.every((light) => state.selection.has(light.id))) return zone.name;
  }
  return `${chosen.length} lights`;
}

async function api(path, body, method) {
  const verb = method || (body ? "POST" : "GET");
  let response;
  try {
    response = await fetch(path, {
      method: verb,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    state.connected = false;
    state.banner = "Not connected to the house";
    scheduleRender();
    throw error;
  }
  if (!response.ok) throw new Error(String(response.status));
  state.connected = true;
  if (state.banner === "Not connected to the house") state.banner = "";
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function hydrate(cache) {
  if (!cache) return;
  state.screen = cache.screen || "home";
  state.findMac = cache.findMac || null;
  state.sheet = Boolean(cache.sheet);
  state.selection = new Set(cache.selection || []);
  state.multi = state.selection.size > 1;
  state.collapsed = new Set(cache.collapsed || []);
  state.home = {
    property: cache.property || "All lights",
    zones: cache.zones || [],
    lights: cache.lights || [],
    pending: [],
    looks: cache.looks || [],
    attention: { icon: null, count: 0 },
    server: {},
  };
  const now = performance.now();
  for (const [id, frame] of Object.entries(cache.frames || {})) {
    state.frames[id] = {
      leds: frame.leds,
      at: now,
      previous: null,
      previousAt: now,
      wall: frame.t || 0,
      fresh: false,
    };
  }
}

function saveCache() {
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    const home = document.querySelector('[data-screen="home"]');
    const frames = {};
    for (const [id, frame] of Object.entries(state.frames)) {
      if (frame.leds) frames[id] = { t: frame.wall || Date.now(), leds: frame.leds.slice(0, 48) };
    }
    const payload = {
      savedAt: Date.now(),
      scroll: home ? home.scrollTop : 0,
      screen: state.screen,
      sheet: state.sheet ? "paint" : null,
      selection: [...state.selection],
      collapsed: [...state.collapsed],
      property: state.home?.property || "All lights",
      zones: state.home?.zones || [],
      lights: lights().map((light) => ({
        id: light.id,
        name: light.name,
        zone_id: light.zone_id,
        position: light.position,
        on: light.on,
        bri: light.bri,
        length: light.length,
        colors: light.colors,
        effect: light.effect,
        palette: light.palette,
        sx: light.sx,
        ix: light.ix,
      })),
      looks: looks(),
      frames,
      findMac: state.findMac,
    };
    try {
      localStorage.setItem("lumen.boot", JSON.stringify(payload));
    } catch (error) {
      /* Storage can be full or blocked. The live view still works. */
    }
    window.__lumenCache = payload;
  }, 60);
}

function scheduleRender() {
  const next = signature();
  if (next === state.signature && document.querySelector("[data-screen]")) {
    patch();
    return;
  }
  state.signature = next;
  const home = document.querySelector('[data-screen="home"]');
  const tray = document.querySelector("[data-look-tray]");
  const scroll = home ? home.scrollTop : 0;
  const trayScroll = tray ? tray.scrollLeft : 0;
  app.innerHTML = view();
  document.body.dataset.route = state.screen;
  const nextHome = document.querySelector('[data-screen="home"]');
  const nextTray = document.querySelector("[data-look-tray]");
  if (nextHome) nextHome.scrollTop = scroll;
  if (nextTray) {
    nextTray.scrollLeft = trayScroll;
    nextTray.addEventListener("scroll", onTrayScroll, { passive: true });
  }
  bindVisible();
  saveCache();
}

function signature() {
  const rows = allLights().map((light) => `${light.id}:${light.name}:${light.zone_id}:${light.position}:${light.named}`).join("|");
  const zones = (state.home?.zones || []).map((zone) => `${zone.id}:${zone.name}`).join("|");
  return [
    state.screen,
    state.sheet,
    state.sheetRatio > 0.75,
    state.solid,
    [...state.selection].join(","),
    [...state.collapsed].join(","),
    state.banner,
    state.findMac,
    state.undo ? 1 : 0,
    state.confirm,
    rows,
    zones,
    state.home?.attention?.icon,
    state.home?.attention?.count,
    state.maint ? 1 : 0,
    JSON.stringify(state.zonePick),
    JSON.stringify(state.ruler),
    state.finder ? `${state.finder.mac}/${state.finder.output}/${state.finder.cuts.join(",")}/${state.finder.saving ? 1 : 0}` : "",
  ].join("~");
}

function patch() {
  document.body.dataset.route = state.screen;
  for (const light of allLights()) {
    const node = document.querySelector(`[data-light="${light.id}"]`);
    if (!node || state.dragging) continue;
    node.classList.toggle("is-off", light.on === false);
    node.classList.toggle("is-offline", light.online === false);
    node.classList.toggle("is-selected", state.selection.has(light.id));
    const reading = node.querySelector(".pct");
    if (reading) reading.textContent = `${pct(light.bri)}%`;
    if (state.selection.has(light.id)) {
      const color = (state.frames[light.id] && dominant(state.frames[light.id].leds)) || light.colors?.[0] || [255, 255, 255];
      node.style.setProperty("--glow", `rgb(${color[0]},${color[1]},${color[2]})`);
    }
  }
  const home = document.querySelector("[data-screen='home'] .home-scroll");
  if (home) home.classList.toggle("has-selection", state.selection.size > 0);
}

function view() {
  return `${statusBar()}<div class="banner" data-banner ${state.banner ? "" : "hidden"}>${escapeHtml(state.banner)}</div><div class="stage">${screen("home", homeView())}${screen("find", findView())}${screen("maintenance", maintView())}</div>${handle()}${sheet()}${undoPill()}`;
}

function screen(name, body) {
  const active = state.screen === name;
  const leave = state.leaving === name;
  const enter = active && state.entered === name;
  return `<section class="screen" data-screen="${name}" data-active="${active ? "true" : "false"}" ${leave ? 'data-leave="true"' : ""} ${enter ? 'data-enter="true"' : ""}>${body}</section>`;
}

function statusBar() {
  const attention = state.home?.attention;
  const icon = attention?.icon;
  const mark = icon
    ? `<button class="status-icon" data-status-alert data-kind="${icon}" aria-label="Maintenance">${icon === "up" ? "↑" : attention.count || ""}</button>`
    : `<button class="status-icon" data-status-alert hidden aria-label="Maintenance"></button>`;
  return `<header class="status" data-status-strip><button class="property" data-property>${escapeHtml(state.home?.property || "All lights")}</button>${mark}</header>`;
}

function homeView() {
  const zones = state.home?.zones || [];
  const groups = new Map(zones.map((zone) => [zone.id, []]));
  const other = [];
  for (const light of lights()) {
    if (light.zone_id && groups.has(light.zone_id)) groups.get(light.zone_id).push(light);
    else other.push(light);
  }
  const blocks = zones.map((zone) => zoneBlock(zone, groups.get(zone.id) || [])).join("");
  const rest = other.length ? zoneBlock({ id: "", name: "Other" }, other) : "";
  const dim = state.sheet ? " is-dimmed" : "";
  const selected = state.selection.size ? " has-selection" : "";
  return `<div class="home-scroll${dim}${selected}">${blocks}${rest}</div>`;
}

function zoneBlock(zone, rows) {
  const collapsed = zone.id && state.collapsed.has(zone.id);
  const average = rows.length ? Math.round(rows.reduce((sum, light) => sum + pct(light.on ? light.bri : 0), 0) / rows.length) : 0;
  const head = zone.id
    ? `<header class="zone-head" data-zone="${zone.id}"><button class="zone-name" data-zone-name>${escapeHtml(zone.name)}</button><span class="zone-bri">${average}%</span><button class="zone-power" data-zone-power>${rows.some((light) => light.on) ? "On" : "Off"}</button></header>`
    : `<header class="zone-head"><span class="zone-name">Other</span></header>`;
  const body = collapsed ? "" : rows.map(lightRow).join("");
  return `<section class="zone">${head}${body}</section>`;
}

function lightRow(light) {
  const selected = state.selection.has(light.id);
  const color = light.colors?.[0] || [255, 255, 255];
  return `<article class="light${light.on === false ? " is-off" : ""}${light.online === false ? " is-offline" : ""}${selected ? " is-selected" : ""}" data-light="${light.id}" style="--glow: rgb(${color[0]},${color[1]},${color[2]})"><canvas class="ribbon" data-ribbon></canvas><span class="light-name" data-light-name="${escapeHtml(light.name)}">${escapeHtml(light.name)}</span><span class="pct">${pct(light.bri)}%</span></article>`;
}

function handle() {
  if (!state.selection.size || state.sheet || state.screen !== "home") return "";
  return `<button class="handle" data-paint-handle><i></i><b data-handle-label>${escapeHtml(selectionLabel())}</b></button>`;
}

function sheet() {
  if (state.screen !== "home") return "";
  const chosen = selectedLights();
  const minis = chosen.slice(0, 4).map((light) => `<span class="mini" data-light="${light.id}"><canvas class="ribbon" data-ribbon></canvas></span>`).join("");
  const many = state.selection.size > MANY ? `<p class="preview-note" data-preview-note>previewing ${state.selection.size} lights</p>` : `<p class="preview-note" data-preview-note></p>`;
  const current = state.draft;
  const intensity = current && String(current.effect).toLowerCase() !== "solid" ? "" : "hidden";
  return `<section class="sheet" data-paint data-open="${state.sheet ? "true" : "false"}" style="--sheet-height:${Math.round(state.sheetRatio * 100)}%"><div class="grab" data-grab></div><div class="selection-head" data-selection-head>${minis}<strong>${escapeHtml(selectionLabel())}</strong><button class="chip" data-select-all>All</button></div><div class="look-tray" data-look-tray ${state.solid ? "hidden" : ""}>${looks().map(swatch).join("")}</div><div class="solid" data-solid ${state.solid ? "" : "hidden"}><div class="temp" data-temperature></div><input class="solid-sat" data-saturation type="range" min="0" max="100" value="80" aria-label="Saturation"></div>${many}<div class="dials"><label class="dial">Brightness<input data-dial="bri" type="range" min="0" max="255" value="${chosen[0]?.bri ?? 128}"></label><label class="dial">Speed<input data-dial="sx" type="range" min="0" max="255" value="${current?.sx ?? 128}"></label><label class="dial" data-intensity ${intensity}>Intensity<input data-dial="ix" type="range" min="0" max="255" value="${current?.ix ?? 128}"></label></div><div class="more" data-more ${state.sheetRatio > 0.78 ? "" : "hidden"}><input data-look-name placeholder="Look name" aria-label="Look name"><button class="text-btn" data-save-look>Save as Look</button></div></section>`;
}

function swatch(look) {
  const current = state.draft && state.draft.id === look.id;
  return `<button class="swatch${current ? " is-current" : ""}" data-look="${look.id}" data-look-name="${escapeHtml(look.name)}"><canvas data-swatch="${look.id}"></canvas><span>${escapeHtml(look.name)}</span></button>`;
}

function undoPill() {
  if (!state.undo) return "";
  return `<button class="undo" data-undo>${escapeHtml(state.undo.label)} · Undo</button>`;
}

function findView() {
  const mac = state.findMac;
  const outputs = allLights().filter((light) => light.mac === mac);
  const board = outputs[0]?.board || {};
  const head = `<button class="back" data-back>Back</button><header class="board-head"><h1>${escapeHtml(board.name || "Board")}</h1><p class="meta">${escapeHtml(board.ip || "")} · ${escapeHtml(board.version || "")} · ${escapeHtml(board.chip || "")}</p><button class="flash" data-flash data-on="false">Flash</button></header>`;
  const byOutput = new Map();
  for (const light of outputs) {
    if (!byOutput.has(light.output_index)) byOutput.set(light.output_index, []);
    byOutput.get(light.output_index).push(light);
  }
  const cards = [...byOutput.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([output, pieces]) => outputCard(output, pieces.sort((a, b) => a.start - b.start)));
  return head + cards.join("");
}

function outputBounds(pieces) {
  const start = Math.min(...pieces.map((piece) => piece.start));
  const stop = Math.max(...pieces.map((piece) => piece.stop));
  return { start, stop, length: Math.max(1, stop - start) };
}

function outputCard(output, pieces) {
  const first = pieces[0];
  const { start, stop, length } = outputBounds(pieces);
  const ruler = state.ruler[first.id] || {};
  const count = ruler.a == null ? "" : Math.abs((ruler.b ?? ruler.a + 25) - ruler.a);
  const finding = state.finder && state.finder.mac === first.mac && state.finder.output === output;
  const title = `<h2 class="output-title">Output ${output + 1}<span>LEDs ${start}–${stop}${pieces.length > 1 ? ` · ${pieces.length} lights` : ""}</span></h2>`;
  const rows = pieces.map(lightRow).join("");
  const naming = pieces.map((piece) => claimForm(output, piece, pieces.length > 1)).join("");
  if (finding) return `<article class="output" data-output="${output}">${title}${rows}${finderPanel(output, pieces)}</article>`;
  return `<article class="output" data-output="${output}">${title}${rows}<div class="ruler" data-ruler="${first.id}">${ticks(length)}</div><p class="span-count" data-span-count>${count === "" ? "" : count}</p><div class="actions"><button class="text-btn" data-only="${output}">Light only this output</button><button class="text-btn" data-set-length="${output}">Set length</button><button class="text-btn" data-finder-open="${output}">${pieces.length > 1 ? "Change segments" : "Split into segments"}</button></div>${naming}</article>`;
}

function claimForm(output, light, split) {
  const key = split ? `l${light.id}` : output;
  const typed = state.names && state.names[key];
  const label = split ? `<p class="piece-label">LEDs ${light.start}–${light.stop}</p>` : "";
  return `<form class="field" data-claim="${output}" data-claim-light="${light.id}" data-name-key="${key}">${label}<input data-output-name placeholder="Name" value="${escapeHtml(typed || (light.named ? light.name : ""))}" aria-label="Light name"><div class="zones-row">${(state.home?.zones || []).map((zone) => `<button type="button" class="chip${state.zonePick[light.id] === zone.id ? " is-on" : ""}" data-zone-choice="${zone.id}">${escapeHtml(zone.name)}</button>`).join("")}<button type="button" class="chip" data-zone-new="${light.id}">New zone</button></div>${state.zonePick[light.id] === "new" ? `<input data-new-zone placeholder="Zone name" aria-label="Zone name">` : ""}<button class="text-btn" type="submit">Done</button></form>`;
}

// ----- segment finder ---------------------------------------------------------

// Same order as PIECE_COLORS on the server, so the screen matches the strip.
const PIECE_COLORS = ["#FF3B30", "#34C759", "#0A84FF", "#FFD60A", "#BF5AF2", "#64D2FF", "#FF9F0A", "#FFFFFF"];

function finderPieces(finder) {
  const edges = [0, ...finder.cuts, finder.length];
  return edges.slice(0, -1).map((low, index) => ({ start: low, stop: edges[index + 1], color: PIECE_COLORS[index % PIECE_COLORS.length] }));
}

function finderPanel(output, pieces) {
  const finder = state.finder;
  const parts = finderPieces(finder);
  const bands = parts
    .map((part) => `<span class="finder-band" style="left:${(part.start / finder.length) * 100}%;width:${((part.stop - part.start) / finder.length) * 100}%;background:${part.color}"></span>`)
    .join("");
  const cuts = finder.cuts.map((cut) => `<span class="finder-cut" style="left:${(cut / finder.length) * 100}%"></span>`).join("");
  const list = parts
    .map((part, index) => {
      const name = finder.names[index] ?? "";
      const remove = index > 0 ? `<button type="button" class="finder-uncut" data-uncut="${part.start}" aria-label="Remove the cut at LED ${part.start}">×</button>` : "";
      return `<li><i style="background:${part.color}"></i><div><input data-piece-name="${index}" value="${escapeHtml(name)}" placeholder="${escapeHtml(pieceName(pieces[0], index))}" aria-label="Name for LEDs ${part.start} to ${part.stop}"><span>LEDs ${part.start}–${part.stop - 1} · ${part.stop - part.start}</span></div>${remove}</li>`;
    })
    .join("");
  const merge = pieces.length > 1 ? `<button class="text-btn" data-split-merge>Merge into one light</button>` : "";
  const save = parts.length > 1 ? `Save ${parts.length} lights` : pieces.length > 1 ? "Save as one light" : "Done";
  return `<section class="finder" data-finder>
<p class="finder-hint">Move the white LED to the first LED of the next run, then cut. The strip shows each piece in its color.</p>
<div class="finder-bar" data-finder-bar>${bands}${cuts}<span class="finder-marker" data-finder-marker style="left:${((finder.marker + 0.5) / finder.length) * 100}%"></span></div>
<p class="finder-led">LED <b data-marker-readout>${finder.marker}</b> <span>of ${finder.length}</span></p>
<div class="finder-steps"><button type="button" data-step="-10">−10</button><button type="button" data-step="-1">−1</button><button type="button" data-step="1">+1</button><button type="button" data-step="10">+10</button></div>
<button class="finder-cut-btn" data-cut ${finder.marker === 0 || finder.cuts.includes(finder.marker) ? "disabled" : ""}>Cut before LED <span data-cut-at>${finder.marker}</span></button>
<ol class="finder-pieces">${list}</ol>
<div class="actions"><button class="text-btn finder-save" data-split-save ${finder.saving ? "disabled" : ""}>${save}</button><button class="text-btn" data-split-cancel>Cancel</button>${merge}</div>
</section>`;
}

function pieceName(first, index) {
  const base = first?.name || "Light";
  return index === 0 ? base : `${base} ${index + 1}`;
}

function openFinder(output) {
  const pieces = allLights().filter((light) => light.mac === state.findMac && light.output_index === output).sort((a, b) => a.start - b.start);
  if (!pieces.length) return;
  const { start, length } = outputBounds(pieces);
  state.finder = {
    mac: state.findMac,
    output,
    length,
    marker: 0,
    cuts: pieces.slice(1).map((piece) => piece.start - start),
    names: pieces.map((piece) => (piece.named ? piece.name : "")),
    saving: false,
  };
  scheduleRender();
  sendFinderPreview(true);
}

let finderTimer = 0;
let finderSentAt = 0;
function sendFinderPreview(now) {
  const finder = state.finder;
  if (!finder) return;
  window.clearTimeout(finderTimer);
  const wait = now ? 0 : Math.max(0, 90 - (performance.now() - finderSentAt));
  finderTimer = window.setTimeout(() => {
    finderSentAt = performance.now();
    api(`/api/lumen/find/${finder.mac}/split/preview`, { output: finder.output, cuts: finder.cuts, marker: finder.marker }).catch(() => {});
  }, wait);
}

function moveMarker(led) {
  const finder = state.finder;
  if (!finder) return;
  const next = Math.max(0, Math.min(finder.length - 1, Math.round(led)));
  if (next === finder.marker) return;
  finder.marker = next;
  haptic(finder.cuts.includes(next) ? "medium" : "light");
  const marker = document.querySelector("[data-finder-marker]");
  if (marker) marker.style.left = `${((next + 0.5) / finder.length) * 100}%`;
  const readout = document.querySelector("[data-marker-readout]");
  if (readout) readout.textContent = String(next);
  const cutAt = document.querySelector("[data-cut-at]");
  if (cutAt) cutAt.textContent = String(next);
  const cut = document.querySelector("[data-cut]");
  if (cut) cut.disabled = next === 0 || finder.cuts.includes(next);
  sendFinderPreview(false);
}

function addCut() {
  const finder = state.finder;
  if (!finder || finder.marker === 0 || finder.cuts.includes(finder.marker)) return;
  const index = finder.cuts.filter((cut) => cut < finder.marker).length + 1;
  finder.cuts = [...finder.cuts, finder.marker].sort((a, b) => a - b);
  finder.names.splice(index, 0, "");
  haptic("medium");
  scheduleRender();
  sendFinderPreview(true);
}

function removeCut(led) {
  const finder = state.finder;
  if (!finder) return;
  const index = finder.cuts.indexOf(led);
  if (index < 0) return;
  finder.cuts.splice(index, 1);
  finder.names.splice(index + 1, 1);
  scheduleRender();
  sendFinderPreview(true);
}

async function closeFinder(save, merge) {
  const finder = state.finder;
  if (!finder) return;
  if (!save) {
    state.finder = null;
    scheduleRender();
    await api(`/api/lumen/find/${finder.mac}/split/cancel`, {}).catch(() => {});
    return;
  }
  finder.saving = true;
  scheduleRender();
  const cuts = merge ? [] : finder.cuts;
  const pieces = allLights().filter((light) => light.mac === finder.mac && light.output_index === finder.output).sort((a, b) => a.start - b.start);
  const names = merge ? [finder.names[0] || ""] : finderPieces(finder).map((_, index) => finder.names[index] || "");
  if (!names[0] && pieces[0]?.named) names[0] = pieces[0].name;
  try {
    await api(`/api/lumen/find/${finder.mac}/split`, { output: finder.output, cuts, names });
    haptic("success");
    state.finder = null;
    await refresh();
  } catch (error) {
    finder.saving = false;
    state.banner = String(error.message || error);
    scheduleRender();
  }
}

function ticks(length) {
  const marks = [];
  const end = Math.max(length, 25);
  for (let led = 0; led <= end; led += 25) {
    const left = (led / end) * 100;
    const major = led % 100 === 0 ? " major" : "";
    const label = led % 50 === 0 ? `<em>${led}</em>` : "";
    marks.push(`<span class="tick${major}" style="left:${left}%"><button type="button" data-tick="${led}" aria-label="${led}"><i></i></button>${label}</span>`);
  }
  return marks.join("");
}

function maintView() {
  const data = state.maint;
  if (!data) return `<button class="back" data-back>Back</button><p class="meta" style="margin:16px">Maintenance</p>`;
  const needs = data.needs || { offline: [], pending: [], firmware: [] };
  const needBlock = [...needs.offline, ...needs.pending, ...needs.firmware].length
    ? `<section class="needs"><h2>Needs attention</h2>${needs.offline.map((item) => row(item.name, `Last seen ${item.last_seen || "—"}`, `<button class="text-btn" data-retry="${item.mac}">Retry</button>`)).join("")}${needs.pending.map((item) => row(item.name, "New board", `<button class="text-btn" data-setup="${item.mac}">Set up</button>`)).join("")}${needs.firmware.map((item) => row(item.name, item.version || "", `<button class="text-btn" data-update="${item.mac}">Update</button>`)).join("")}${needs.firmware.length > 1 ? `<button class="amber" data-update-all>Update all</button>` : ""}</section>`
    : "";
  const boards = (data.boards || []).map((board) => row(
    board.name,
    `${board.ip || ""} · ${board.chip || ""} · ${board.version || ""} · ${board.outputs} outputs`,
    `<span class="dot${board.online ? " on" : ""}"></span>`,
    `data-board="${board.mac}"`,
  )).join("");
  const backups = (data.backups || []).map((group) => `<section class="backup"><div><strong>${escapeHtml(group.name)}</strong>${group.items.slice(0, 8).map((item) => `<p class="meta">${escapeHtml(item.created_at)} · ${escapeHtml(item.reason)} <button class="danger" data-restore="${item.id}">Restore</button></p>`).join("")}</div></section>`).join("");
  const server = data.server || {};
  const confirm = state.confirm
    ? `<div class="confirm" data-confirm><p>Restore this backup?</p><button class="danger" data-restore-go="${state.confirm}">Restore</button><button class="text-btn" data-confirm-cancel>Cancel</button></div>`
    : "";
  return `<button class="back" data-back>Back</button><div class="maint">${needBlock}<h2>Boards</h2>${boards}<h2>Backups</h2>${backups}${confirm}<h2>Server</h2><section class="server"><p class="meta">${escapeHtml(server.version || "")} · up ${formatUptime(server.uptime)}</p><label class="meta"><input data-mirror type="checkbox" ${server.mirror_looks ? "checked" : ""}> Mirror looks to presets</label><input data-property-name value="${escapeHtml(server.property || "")}" aria-label="Property name"><button class="text-btn" data-rescan>Rescan now</button></section></div>`;
}

function row(title, meta, extra, attrs = "") {
  return `<div class="row" ${attrs}><div><strong>${escapeHtml(title)}</strong><p class="meta">${escapeHtml(meta)}</p></div><div>${extra}</div></div>`;
}

function formatUptime(seconds) {
  const value = Number(seconds || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function go(name, extra) {
  if (state.screen === name) return;
  state.leaving = state.screen;
  state.entered = name;
  state.screen = name;
  if (extra) Object.assign(state, extra);
  scheduleRender();
  window.setTimeout(() => {
    state.leaving = null;
    state.entered = null;
    scheduleRender();
  }, 320);
  if (name === "maintenance") loadMaint();
  saveCache();
}

async function loadMaint() {
  try {
    state.maint = await api("/api/lumen/maintenance");
    state.signature = "";
    scheduleRender();
  } catch (error) {
    state.banner = "Not connected to the house";
    scheduleRender();
  }
}

async function refresh() {
  const home = await api("/api/lumen/home");
  const previous = new Map(allLights().map((light) => [light.id, light]));
  state.home = home;
  for (const light of allLights()) {
    const prior = previous.get(light.id);
    if (prior && state.override[light.id]) {
      light.bri = state.override[light.id].bri ?? light.bri;
      light.on = state.override[light.id].on ?? light.on;
    }
  }
  if (!state.draft && looks()[0]) state.draft = { ...looks()[0] };
  scheduleRender();
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let socket;
  let delay = 400;
  const open = () => {
    socket = new WebSocket(`${proto}://${location.host}/api/lumen/ws`);
    socket.addEventListener("open", () => {
      state.connected = true;
      state.banner = "";
      delay = 400;
      sendVisible();
      scheduleRender();
    });
    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (message.type === "frames") takeFrames(message.items || []);
      if (message.type === "state" && !state.scrubbing) takeState(message.lights || []);
    });
    socket.addEventListener("close", () => {
      state.connected = false;
      state.banner = "Not connected to the house";
      scheduleRender();
      window.setTimeout(open, delay);
      delay = Math.min(8000, delay * 1.6);
    });
  };
  open();
  state.socket = () => socket;
}

function sendVisible() {
  const socket = state.socket && state.socket();
  if (!socket || socket.readyState !== 1) return;
  const ids = [...document.querySelectorAll("[data-light]")].filter((node) => node.dataset.visible !== "0").map((node) => Number(node.dataset.light));
  socket.send(JSON.stringify({ type: "visible", ids }));
}

function bindVisible() {
  if (!("IntersectionObserver" in window)) {
    sendVisible();
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) entry.target.dataset.visible = entry.isIntersecting ? "1" : "0";
    sendVisible();
  }, { root: null, threshold: 0.15 });
  document.querySelectorAll("[data-light]").forEach((node) => observer.observe(node));
}

function takeFrames(items) {
  const now = performance.now();
  for (const item of items) {
    const prior = state.frames[item.id];
    state.frames[item.id] = {
      leds: item.leds,
      at: now,
      previous: prior ? prior.leds : null,
      previousAt: prior ? prior.at : now,
      wall: Date.now(),
      fresh: true,
    };
    if (!state.dragging && !state.scrubbing) delete state.override[item.id];
  }
}

function takeState(rows) {
  let changed = false;
  for (const row of rows) {
    const light = lightById(row.id);
    if (!light) continue;
    Object.assign(light, row);
    changed = true;
  }
  if (changed) patch();
}

function rememberUndo(label, items) {
  state.undo = { label, items, at: Date.now() };
  window.clearTimeout(state.undoTimer);
  state.undoTimer = window.setTimeout(() => {
    state.undo = null;
    scheduleRender();
  }, 4000);
  scheduleRender();
}

async function performUndo() {
  const action = state.undo;
  if (!action) return;
  state.undo = null;
  for (const item of action.items) {
    state.flow[item.id] = { from: state.frames[item.id]?.leds, start: performance.now(), reverse: true };
    state.override[item.id] = { ...item, effect: item.effect || "Solid" };
    const light = lightById(item.id);
    if (light) {
      light.on = item.on;
      light.bri = item.bri;
      light.colors = item.colors;
      light.effect = item.effect;
    }
    await api("/api/lumen/apply", {
      light_ids: [item.id],
      kind: "commit",
      on: item.on,
      bri: item.bri,
      tt_ms: COMMIT_MS,
      look: {
        effect: item.effect || "Solid",
        fallback: "Solid",
        palette: item.palette || "Default",
        colors: item.colors || [[255, 214, 170], [0, 0, 0], [0, 0, 0]],
        sx: item.sx ?? 128,
        ix: item.ix ?? 128,
      },
    });
  }
  scheduleRender();
}

function snapshot(ids) {
  return ids.map((id) => {
    const light = lightById(id) || {};
    return {
      id,
      on: light.on !== false,
      bri: light.bri ?? 128,
      colors: light.colors,
      effect: light.effect,
      palette: light.palette,
      sx: light.sx,
      ix: light.ix,
    };
  });
}

async function sendApply(body) {
  const ids = body.light_ids || [];
  if (body.kind === "commit") {
    for (const id of ids) {
      const light = lightById(id);
      state.flow[id] = { from: lastDrawn[id], start: performance.now(), reverse: false };
      if (light && body.bri != null) light.bri = body.bri;
      if (light && body.on != null) light.on = body.on;
    }
  }
  return api("/api/lumen/apply", body);
}

function openSheet() {
  state.sheet = true;
  if (!state.draft && looks()[0]) state.draft = { ...looks()[0] };
  scheduleRender();
}

function closeSheet() {
  state.sheet = false;
  state.scrubbing = false;
  scheduleRender();
}

function selectOnly(id) {
  state.selection = new Set([id]);
  state.multi = false;
  openSheet();
  haptic("light");
}

function toggleSelect(id) {
  if (state.selection.has(id)) state.selection.delete(id);
  else state.selection.add(id);
  state.multi = state.selection.size > 0;
  if (!state.selection.size) state.sheet = false;
  haptic("light");
  scheduleRender();
}

async function commitLook(look) {
  if (!look || !state.selection.size) return;
  const ids = [...state.selection];
  const before = snapshot(ids);
  state.draft = { ...look };
  state.solid = String(look.effect).toLowerCase() === "solid";
  state.scrubbing = false;
  for (const id of ids) {
    const light = lightById(id);
    state.override[id] = { ...look, on: true, bri: light?.bri ?? 255 };
    if (light) {
      light.effect = look.effect;
      light.colors = look.colors;
      light.on = true;
    }
  }
  rememberUndo(`${selectionLabel()} → ${look.name}`, before);
  await sendApply({
    light_ids: ids,
    kind: "commit",
    look_id: look.id,
    on: true,
    tt_ms: COMMIT_MS,
    look: {
      effect: look.effect,
      fallback: look.fallback,
      palette: look.palette,
      colors: look.colors,
      sx: look.sx,
      ix: look.ix,
    },
  });
  haptic("light");
}

function previewLook(look, moving) {
  if (!look || !state.selection.size) return;
  const now = performance.now();
  if (moving) lastScrubMove = now;
  if (now - lastScrubMove > 2000) return;
  const gap = state.selection.size > MANY ? PREVIEW_MANY_MS : PREVIEW_MS;
  if (now - lastPreview < gap) return;
  lastPreview = now;
  state.scrubbing = true;
  state.draft = { ...look };
  markCurrent(look.id);
  for (const id of state.selection) {
    const light = lightById(id);
    state.override[id] = { ...look, on: true, bri: light?.bri ?? 255 };
  }
  const note = document.querySelector("[data-preview-note]");
  if (note) note.textContent = state.selection.size > MANY ? `previewing ${state.selection.size} lights` : "";
  sendApply({
    light_ids: [...state.selection],
    kind: "preview",
    look_id: look.id,
    on: true,
    tt_ms: 0,
    look: { effect: look.effect, fallback: look.fallback, palette: look.palette, colors: look.colors, sx: look.sx, ix: look.ix },
  }).catch(() => {});
}

function markCurrent(id) {
  document.querySelectorAll("[data-look]").forEach((node) => node.classList.toggle("is-current", Number(node.dataset.look) === Number(id)));
}

function centeredLook(tray) {
  const mid = tray.getBoundingClientRect().left + tray.clientWidth / 2;
  let best = null;
  let distance = Infinity;
  for (const node of tray.querySelectorAll("[data-look]")) {
    const rect = node.getBoundingClientRect();
    const delta = Math.abs(rect.left + rect.width / 2 - mid);
    if (delta < distance) {
      distance = delta;
      best = node;
    }
  }
  return best ? lookById(best.dataset.look) : null;
}

function onTrayScroll(event) {
  if (!trayPointer) return;
  const look = centeredLook(event.currentTarget);
  if (look) {
    haptic("light");
    previewLook(look, true);
  }
}

const lastDrawn = {};
let auditAt = 0;

function tick(now) {
  if (now - auditAt > 32) {
    auditAt = now;
    auditScreens();
  }
  document.querySelectorAll("[data-ribbon]").forEach((canvas) => {
    const host = canvas.closest("[data-light]");
    if (!host || host.dataset.visible === "0") return;
    const id = Number(host.dataset.light);
    const light = lightById(id);
    if (!light) return;
    const frame = state.frames[id];
    const identify = state.identify[id];
    let pixels;
    if (identify && now - identify < 1200) {
      const phase = Math.floor((now - identify) / 200);
      pixels = phase % 2 === 0 ? [[255, 255, 255], [255, 255, 255], [255, 255, 255], [255, 255, 255]] : [[12, 12, 14], [12, 12, 14]];
    } else if (state.override[id]) {
      pixels = renderEffect({ ...state.override[id], on: state.override[id].on !== false, bri: state.override[id].bri ?? light.bri }, 48, now);
    } else if (frame && frame.leds) {
      pixels = frameAt(frame, now);
    } else {
      pixels = renderEffect({ ...light, effect: light.effect || "Solid", on: light.on, bri: light.bri }, 48, now);
    }
    const flow = state.flow[id];
    const flowDraw = flow ? { from: flow.from, amount: Math.min(1, (now - flow.start) / COMMIT_MS) } : null;
    if (flowDraw && flow.reverse) flowDraw.amount = 1 - flowDraw.amount;
    const stale = !frame || frame.fresh === false || Date.now() - (frame.wall || 0) > STALE_MS;
    drawRibbon(canvas, pixels, { now, flow: flowDraw && flowDraw.amount < 1 ? flowDraw : null, desaturate: stale || light.online === false });
    lastDrawn[id] = pixels;
    if (flow && now - flow.start > COMMIT_MS) delete state.flow[id];
    if (identify && now - identify >= 1200) delete state.identify[id];
  });
  document.querySelectorAll("[data-swatch]").forEach((canvas) => {
    const look = lookById(canvas.dataset.swatch);
    if (!look) return;
    const pixels = renderEffect({ ...look, on: true, bri: 255 }, 24, now);
    drawRibbon(canvas, pixels, { now });
  });
  requestAnimationFrame(tick);
}

function auditScreens() {
  const visible = [...document.querySelectorAll("[data-screen]")].filter((node) => {
    const rect = node.getBoundingClientRect();
    const width = Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0);
    const height = Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0);
    return width > window.innerWidth * 0.72 && height > window.innerHeight * 0.7;
  });
  if (visible.length > 1) window.__screenViolations += 1;
}

function onPointerDown(event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  gesture[event.pointerId] = {
    x: event.clientX,
    y: event.clientY,
    t: performance.now(),
    target: event.target,
    light: event.target.closest?.("[data-light]")?.dataset.light,
    zone: event.target.closest?.("[data-zone]")?.dataset.zone,
    mode: null,
  };
  if (event.target.closest?.("[data-look-tray]")) trayPointer = true;
  const active = Object.keys(gesture);
  if (active.length === 1 && gesture[event.pointerId].light && !event.target.closest("input, button.zone-power")) {
    const id = gesture[event.pointerId].light;
    gesture[event.pointerId].timer = window.setTimeout(() => {
      const current = gesture[event.pointerId];
      if (!current || current.mode) return;
      current.mode = "long";
      if (event.target.closest("[data-light-name]")) beginRename(id);
      else {
        toggleSelect(Number(id));
        current.mode = "drag";
      }
    }, LONG_MS);
  }
}

function onPointerMove(event) {
  const current = gesture[event.pointerId];
  if (!current) return;
  const dx = event.clientX - current.x;
  const dy = event.clientY - current.y;
  if (current.target.closest?.("[data-look-tray]")) {
    const tray = document.querySelector("[data-look-tray]");
    if (tray) {
      if (current.lastX == null) current.lastX = current.x;
      const delta = event.clientX - current.lastX;
      current.lastX = event.clientX;
      tray.scrollLeft -= delta;
      const look = centeredLook(tray);
      if (look && look.id !== current.lookId) {
        current.lookId = look.id;
        haptic("light");
      }
      if (look) previewLook(look, true);
    }
    return;
  }
  if (!current.mode && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) window.clearTimeout(current.timer);
  if (!current.mode) {
    if (Math.abs(dy) > CANCEL_Y && Math.abs(dy) > Math.abs(dx)) current.mode = "scroll";
    else if (Math.abs(dx) >= ENGAGE_X && Math.abs(dy) < CANCEL_Y && current.light) {
      current.mode = "brightness";
      current.origin = lightById(current.light)?.bri ?? 128;
      state.dragging = true;
      window.clearTimeout(current.timer);
    }
  }
  if (current.mode === "brightness" && current.light) {
    const light = lightById(current.light);
    const width = event.target.closest("[data-light]")?.clientWidth || 320;
    let bri = Math.round(current.origin + (dx / width) * 255);
    bri = Math.max(0, Math.min(255, bri));
    if ((bri === 0 || bri === 255) && current.edge !== bri) {
      current.edge = bri;
      haptic("medium");
    }
    if (light) {
      light.bri = bri;
      light.on = bri > 0;
    }
    state.override[current.light] = { ...(state.override[current.light] || light || {}), bri, on: bri > 0, effect: light?.effect || "Solid", colors: light?.colors };
    const node = document.querySelector(`[data-light="${current.light}"] .pct`);
    if (node) node.textContent = `${pct(bri)}%`;
    const now = performance.now();
    if (now - (current.sent || 0) > 100) {
      current.sent = now;
      sendApply({ light_ids: [Number(current.light)], kind: "preview", bri, on: bri > 0, tt_ms: 0 }).catch(() => {});
    }
  }
  if (current.mode === "drag" && current.light) {
    const node = document.querySelector(`[data-light="${current.light}"]`);
    if (node) node.style.transform = `translateY(${dy}px)`;
  }
  const grab = current.target.closest?.("[data-grab]");
  if (grab && state.sheet) {
    const ratio = Math.max(0.42, Math.min(0.9, state.sheetRatio - dy / window.innerHeight));
    state.sheetRatio = ratio;
    const sheetNode = document.querySelector("[data-paint]");
    if (sheetNode) sheetNode.style.setProperty("--sheet-height", `${Math.round(ratio * 100)}%`);
    const more = document.querySelector("[data-more]");
    if (more) more.hidden = ratio <= 0.78;
  }
}

function onPointerUp(event) {
  const current = gesture[event.pointerId];
  delete gesture[event.pointerId];
  if (!current) return;
  window.clearTimeout(current.timer);
  const dx = event.clientX - current.x;
  const dy = event.clientY - current.y;
  const dt = performance.now() - current.t;
  const pointers = Object.keys(gesture).length;
  if (pointers === 0 && event.pointerType !== "mouse") {
    const downs = Math.abs(dy);
    if (downs > 64 && Math.abs(dx) < 40 && Object.keys(gesture).length === 0 && event.isPrimary === false) performUndo();
  }
  if (current.target.closest?.("[data-look-tray]") || trayPointer) {
    const tray = document.querySelector("[data-look-tray]");
    trayPointer = false;
    const tapped = current.target.closest?.("[data-look]");
    const look = Math.abs(dx) > 8 && tray ? centeredLook(tray) : tapped ? lookById(tapped.dataset.look) : tray ? centeredLook(tray) : null;
    if (look) commitLook(look);
    return;
  }
  if (current.mode === "brightness" && current.light) {
    state.dragging = false;
    const light = lightById(current.light);
    const flick = dt < FLICK_MS && dx > FLICK_MIN && dx < FLICK_MAX && Math.abs(dy) < 18;
    if (flick && light) {
      light.bri = current.origin;
      const turningOn = !light.on;
      light.on = turningOn;
      if (turningOn && light.bri < 8) light.bri = current.origin || 128;
      haptic("medium");
      const before = snapshot([Number(current.light)]);
      before[0].on = !light.on;
      before[0].bri = current.origin;
      rememberUndo(`${light.name} → ${light.on ? "On" : "Off"}`, before);
      sendApply({ light_ids: [Number(current.light)], kind: "commit", on: light.on, bri: light.bri, tt_ms: light.on ? 300 : 500 }).catch(() => {});
    } else if (light) {
      const before = snapshot([Number(current.light)]);
      before[0].bri = current.origin;
      rememberUndo(`${light.name} → ${pct(light.bri)}%`, before);
      sendApply({ light_ids: [Number(current.light)], kind: "commit", bri: light.bri, on: light.bri > 0, tt_ms: light.bri === 0 ? 500 : 0 }).catch(() => {});
    }
    scheduleRender();
    return;
  }
  if (current.mode === "drag" && current.light) {
    const node = document.querySelector(`[data-light="${current.light}"]`);
    if (node) node.style.transform = "";
    const hit = document.elementFromPoint(event.clientX, event.clientY)?.closest?.("[data-light]");
    if (hit && hit.dataset.light !== current.light) dropLight(Number(current.light), Number(hit.dataset.light));
    return;
  }
  if (current.mode === "scroll" || current.mode === "long") return;
  if (Math.abs(dx) > 8 || Math.abs(dy) > 8) return;
  if (event.target.closest("input, textarea, [data-dial], [data-temperature], [data-grab], [data-finder-bar]")) return;
  handleTap(event, current);
}

function handleTap(event, current) {
  const tick = event.target.closest("[data-tick]");
  if (tick) return onTick(tick);
  if (event.target.closest("[data-paint-handle]")) return openSheet();
  if (event.target.closest("[data-select-all]")) {
    state.selection = new Set(lights().map((light) => light.id));
    state.multi = true;
    openSheet();
    return;
  }
  // The status strip is the way to Maintenance (and Find) even when nothing needs attention.
  if (event.target.closest("[data-status-alert], [data-property]")) return go("maintenance");
  if (event.target.closest("[data-back]")) {
    if (state.finder) closeFinder(false);
    return go("home");
  }
  if (event.target.closest("[data-undo]")) return performUndo();
  const zoneName = event.target.closest("[data-zone-name]");
  if (zoneName && state.selection.size === 0) {
    const id = Number(zoneName.closest("[data-zone]").dataset.zone);
    if (state.collapsed.has(id)) state.collapsed.delete(id);
    else state.collapsed.add(id);
    scheduleRender();
    return;
  }
  const zone = event.target.closest("[data-zone]");
  if (zone && !event.target.closest("[data-zone-power]")) {
    const id = Number(zone.dataset.zone);
    state.selection = new Set(lights().filter((light) => light.zone_id === id).map((light) => light.id));
    state.multi = state.selection.size > 1;
    openSheet();
    return;
  }
  if (event.target.closest("[data-zone-power]")) return powerZone(Number(event.target.closest("[data-zone]").dataset.zone));
  const board = event.target.closest("[data-board]");
  if (board) {
    state.findMac = board.dataset.board;
    return go("find");
  }
  if (event.target.closest("[data-setup]")) {
    state.findMac = event.target.closest("[data-setup]").dataset.setup;
    return go("find");
  }
  const lightNode = event.target.closest("[data-light]");
  if (lightNode && state.screen === "home" && !event.target.closest("[data-light-name] input")) {
    const id = Number(lightNode.dataset.light);
    const now = performance.now();
    if (state.lastTap && now - state.lastTap.at < 300 && state.lastTap.id === id) {
      identify(id);
      state.lastTap = null;
      return;
    }
    state.lastTap = { at: now, id };
    if (state.multi) toggleSelect(id);
    else selectOnly(id);
    return;
  }
  const look = event.target.closest("[data-look]");
  if (look) {
    const chosen = lookById(look.dataset.look);
    if (chosen && String(chosen.effect).toLowerCase() === "solid") {
      state.solid = true;
      state.draft = { ...chosen };
      scheduleRender();
    }
  }
}

async function powerZone(zoneId) {
  const rows = lights().filter((light) => light.zone_id === zoneId);
  if (!rows.length) return;
  const turningOn = rows.every((light) => !light.on);
  const before = snapshot(rows.map((light) => light.id));
  rememberUndo(`${rows[0] ? state.home.zones.find((zone) => zone.id === zoneId)?.name : "Zone"} → ${turningOn ? "On" : "Off"}`, before);
  haptic("medium");
  for (const light of rows) {
    light.on = turningOn;
    if (turningOn && light.bri < 8) light.bri = 128;
  }
  await sendApply({ light_ids: rows.map((light) => light.id), kind: "commit", on: turningOn, bri: turningOn ? 128 : 0, tt_ms: turningOn ? 300 : 500 });
  scheduleRender();
}

function beginRename(id) {
  const light = lightById(id);
  const node = document.querySelector(`[data-light="${id}"] .light-name`);
  if (!light || !node) return;
  node.innerHTML = `<input class="rename" data-rename="${id}" value="${escapeHtml(light.name)}" />`;
  const input = node.querySelector("input");
  input.focus();
  input.select();
}

async function finishRename(input) {
  const id = Number(input.dataset.rename);
  const name = input.value.trim();
  if (!name) return scheduleRender();
  const light = lightById(id);
  if (light) light.name = name;
  await api(`/api/lumen/lights/${id}`, { name });
  scheduleRender();
}

async function identify(id) {
  state.identify[id] = performance.now();
  haptic("light");
  api(`/api/lumen/lights/${id}/identify`, {}).catch(() => {});
}

async function dropLight(sourceId, targetId) {
  const target = lightById(targetId);
  if (!target) return;
  haptic("medium");
  if (target.zone_id) {
    await api("/api/lumen/order", { lights: [{ id: sourceId, zone_id: target.zone_id, position: target.position }] });
  } else {
    const count = (state.home?.zones || []).length + 1;
    await api("/api/lumen/zones", { name: count === 1 ? "Zone" : `Zone ${count}`, light_ids: [sourceId, targetId] });
  }
  await refresh();
}

function onTick(node) {
  const ruler = node.closest("[data-ruler]");
  const light = lightById(ruler.dataset.ruler);
  if (!light) return;
  const led = Number(node.dataset.tick);
  const current = state.ruler[light.id] || {};
  if (current.a == null || current.b != null) state.ruler[light.id] = { a: led, b: null };
  else state.ruler[light.id] = { a: current.a, b: led };
  const span = state.ruler[light.id];
  const start = span.b == null ? span.a : Math.min(span.a, span.b);
  const stop = span.b == null ? span.a + 25 : Math.max(span.a, span.b);
  const count = document.querySelector(`[data-ruler="${light.id}"]`)?.parentElement?.querySelector("[data-span-count]");
  if (count) count.textContent = String(Math.abs(stop - start));
  api(`/api/lumen/find/${light.mac}/span`, { output: light.output_index, start, stop: Math.max(start + 1, stop) }).catch(() => {});
}

function colorFromBand(amount, saturation) {
  const sat = saturation / 100;
  if (amount <= 0.5) {
    const t = amount / 0.5;
    return mixColor([255, 176, 90], [206, 222, 255], t);
  }
  const hue = (amount - 0.5) / 0.5;
  return mixColor([255, 255, 255], hsl(hue, 1, 0.5), sat);
}

function mixColor(a, b, t) {
  return a.map((channel, index) => Math.round(channel + (b[index] - channel) * t));
}

function hsl(h, s, l) {
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h * 12) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
  };
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

// Drag along the bar to walk the marker; the real strip follows.
function onFinderBar(event) {
  const bar = event.target.closest?.("[data-finder-bar]");
  if (!bar || !state.finder) return;
  const at = (point) => {
    const rect = bar.getBoundingClientRect();
    moveMarker(((point.clientX - rect.left) / rect.width) * state.finder.length - 0.5);
  };
  bar.setPointerCapture?.(event.pointerId);
  at(event);
  const move = (next) => at(next);
  const up = () => {
    bar.removeEventListener("pointermove", move);
    bar.removeEventListener("pointerup", up);
    bar.removeEventListener("pointercancel", up);
  };
  bar.addEventListener("pointermove", move);
  bar.addEventListener("pointerup", up);
  bar.addEventListener("pointercancel", up);
}

function bind() {
  document.addEventListener("pointerdown", onFinderBar);
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);
  document.addEventListener("click", (event) => {
    const restore = event.target.closest("[data-restore]");
    if (restore && !event.target.closest("[data-restore-go]")) {
      state.confirm = restore.dataset.restore;
      scheduleRender();
    }
  });
  document.addEventListener("change", async (event) => {
    if (event.target.matches("[data-mirror]")) {
      await api("/api/lumen/settings", { mirror_looks: event.target.checked });
    }
    if (event.target.matches("[data-dial]")) dial(event.target.dataset.dial, Number(event.target.value), true);
  });
  document.addEventListener("input", (event) => {
    if (event.target.matches("[data-piece-name]") && state.finder) state.finder.names[Number(event.target.dataset.pieceName)] = event.target.value;
    if (event.target.matches("[data-dial]")) dial(event.target.dataset.dial, Number(event.target.value), false);
    if (event.target.matches("[data-property-name]")) {
      window.clearTimeout(event.target._timer);
      event.target._timer = window.setTimeout(() => {
        api("/api/lumen/settings", { property_name: event.target.value }).then(refresh).catch(() => {});
      }, 400);
    }
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-claim]");
    if (!form) return;
    event.preventDefault();
    const output = Number(form.dataset.claim);
    const card = form.closest("[data-output]");
    const lightId = Number(form.dataset.claimLight);
    const light = allLights().find((item) => item.id === lightId) || allLights().find((item) => item.mac === state.findMac && item.output_index === output);
    if (!light) return;
    const name = form.querySelector("[data-output-name]").value.trim();
    if (!name) return;
    const pick = state.zonePick[light.id];
    const body = { output, name, light_id: light.id };
    if (pick === "new") body.zone_name = form.querySelector("[data-new-zone]")?.value.trim() || "Zone";
    else if (pick) body.zone_id = Number(pick);
    await api(`/api/lumen/find/${light.mac}/claim`, body);
    await refresh();
    go("home");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches("[data-rename]")) {
      event.preventDefault();
      finishRename(event.target);
    }
  });
  document.addEventListener("click", async (event) => {
    if (event.target.matches("[data-zone-choice]")) {
      const card = event.target.closest("form");
      const light = lightById(card.dataset.claimLight);
      if (light) state.zonePick[light.id] = Number(event.target.dataset.zoneChoice);
      scheduleRender();
    }
    if (event.target.matches("[data-zone-new]")) {
      const form = event.target.closest("form");
      const typed = form?.querySelector("[data-output-name]")?.value;
      state.names = state.names || {};
      if (typed) state.names[form.dataset.nameKey] = typed;
      state.zonePick[event.target.dataset.zoneNew] = "new";
      scheduleRender();
    }
    if (event.target.closest("[data-finder-open]")) openFinder(Number(event.target.closest("[data-finder-open]").dataset.finderOpen));
    if (event.target.closest("[data-step]")) moveMarker((state.finder?.marker || 0) + Number(event.target.closest("[data-step]").dataset.step));
    if (event.target.closest("[data-cut]")) addCut();
    if (event.target.closest("[data-uncut]")) removeCut(Number(event.target.closest("[data-uncut]").dataset.uncut));
    if (event.target.closest("[data-split-save]")) closeFinder(true, false);
    if (event.target.closest("[data-split-merge]")) closeFinder(true, true);
    if (event.target.closest("[data-split-cancel]")) closeFinder(false);
    if (event.target.matches("[data-only]")) {
      const light = allLights().find((item) => item.mac === state.findMac && item.output_index === Number(event.target.dataset.only));
      if (light) api(`/api/lumen/find/${light.mac}/only`, { output: light.output_index }).catch(() => {});
    }
    if (event.target.matches("[data-set-length]")) {
      const output = Number(event.target.dataset.setLength);
      const light = allLights().find((item) => item.mac === state.findMac && item.output_index === output);
      const span = state.ruler[light?.id] || {};
      const whole = outputBounds(allLights().filter((item) => item.mac === state.findMac && item.output_index === output)).length;
      const length = span.a == null ? whole : Math.max(1, Math.abs((span.b ?? span.a + 25) - span.a));
      if (light) {
        await api(`/api/lumen/find/${light.mac}/length`, { output, length });
        await refresh();
      }
    }
    if (event.target.matches("[data-flash]")) {
      const result = await api(`/api/lumen/find/${state.findMac}/flash`, {});
      event.target.dataset.on = result.flashing ? "true" : "false";
      event.target.textContent = result.flashing ? "Stop" : "Flash";
    }
    if (event.target.matches("[data-rescan]")) {
      await api("/api/lumen/rescan", {});
      await refresh();
      await loadMaint();
    }
    if (event.target.matches("[data-update]")) api("/api/lumen/firmware", { macs: [event.target.dataset.update] }).catch(() => {});
    if (event.target.matches("[data-update-all]")) api("/api/lumen/firmware", { macs: [] }).catch(() => {});
    if (event.target.matches("[data-restore-go]")) {
      await api(`/api/lumen/backups/${event.target.dataset.restoreGo}/restore`, {});
      state.confirm = null;
      await refresh();
      await loadMaint();
    }
    if (event.target.matches("[data-confirm-cancel]")) {
      state.confirm = null;
      scheduleRender();
    }
    if (event.target.matches("[data-save-look]")) {
      const name = document.querySelector("[data-look-name]")?.value.trim();
      const draft = state.draft || looks()[0];
      if (!name || !draft) return;
      await api("/api/lumen/looks", {
        name,
        effect: draft.effect,
        fallback: draft.fallback || "Solid",
        palette: draft.palette || "Default",
        colors: draft.colors,
        sx: draft.sx,
        ix: draft.ix,
      });
      haptic("success");
      await refresh();
    }
    if (event.target.matches("[data-retry]")) api("/api/lumen/rescan", {}).then(refresh).catch(() => {});
  });
  document.addEventListener("pointerdown", (event) => {
    const band = event.target.closest("[data-temperature]");
    if (!band) return;
    const move = (point) => {
      const rect = band.getBoundingClientRect();
      const amount = Math.max(0, Math.min(1, (point.clientX - rect.left) / rect.width));
      const sat = Number(document.querySelector("[data-saturation]")?.value || 80);
      const color = colorFromBand(amount, sat);
      paintColor(color, false);
    };
    move(event);
    const drag = (point) => move(point);
    const up = () => {
      const rect = band.getBoundingClientRect();
      const amount = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      paintColor(colorFromBand(amount, Number(document.querySelector("[data-saturation]")?.value || 80)), true);
      window.removeEventListener("pointermove", drag);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", drag);
    window.addEventListener("pointerup", up);
  });
  let edge = null;
  document.addEventListener("pointerdown", (event) => {
    if (event.clientX < 24 && state.screen !== "home") edge = event.clientX;
  });
  document.addEventListener("pointerup", (event) => {
    if (edge != null && event.clientX - edge > 72) go("home");
    edge = null;
  });
}

let colorSent = 0;
function paintColor(color, commit) {
  const solid = looks().find((look) => String(look.effect).toLowerCase() === "solid") || looks()[0];
  if (!solid) return;
  const look = { ...solid, colors: [color, [0, 0, 0], [0, 0, 0]], effect: "Solid" };
  state.draft = look;
  const now = performance.now();
  if (!commit && now - colorSent < 100) {
    for (const id of state.selection) state.override[id] = { ...look, on: true, bri: lightById(id)?.bri ?? 255 };
    return;
  }
  colorSent = now;
  if (commit) commitLook(look);
  else previewLook(look, true);
}

let dialSent = 0;
function dial(name, value, commit) {
  const ids = [...state.selection];
  if (!ids.length) return;
  if (name === "bri") {
    for (const id of ids) {
      const light = lightById(id);
      if (light) {
        light.bri = value;
        light.on = value > 0;
      }
      state.override[id] = { ...(state.override[id] || light), bri: value, on: value > 0 };
    }
    const now = performance.now();
    if (!commit && now - dialSent < 100) return;
    dialSent = now;
    if (commit) {
      const before = snapshot(ids);
      rememberUndo(`${selectionLabel()} → ${pct(value)}%`, before);
    }
    sendApply({ light_ids: ids, kind: commit ? "commit" : "preview", bri: value, on: value > 0, tt_ms: commit ? 0 : 0 }).catch(() => {});
    return;
  }
  if (!state.draft) return;
  state.draft = { ...state.draft, [name]: value };
  for (const id of ids) state.override[id] = { ...state.draft, on: true, bri: lightById(id)?.bri ?? 255 };
  const now = performance.now();
  if (!commit && now - dialSent < 100) return;
  dialSent = now;
  const body = {
    light_ids: ids,
    kind: commit ? "commit" : "preview",
    look_id: state.draft.id,
    on: true,
    tt_ms: commit ? COMMIT_MS : 0,
    look: { sx: state.draft.sx, ix: state.draft.ix, colors: state.draft.colors, effect: state.draft.effect, fallback: state.draft.fallback, palette: state.draft.palette },
  };
  if (commit) rememberUndo(`${selectionLabel()} → ${name === "sx" ? "Speed" : "Intensity"}`, snapshot(ids));
  sendApply(body).catch(() => {});
}

function installStandalone() {
  const query = window.matchMedia("(display-mode: standalone)");
  const apply = () => document.documentElement.classList.toggle("standalone", query.matches || navigator.standalone === true);
  apply();
  query.addEventListener?.("change", apply);
}

async function start() {
  installStandalone();
  hydrate(window.__lumenCache || readStored());
  bind();
  scheduleRender();
  requestAnimationFrame(tick);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  connect();
  try {
    await refresh();
  } catch (error) {
    state.connected = false;
    state.banner = state.home ? "Not connected to the house" : "";
    scheduleRender();
  }
}

function readStored() {
  try {
    return JSON.parse(localStorage.getItem("lumen.boot") || "null");
  } catch (error) {
    return null;
  }
}

document.addEventListener("visibilitychange", saveCache);
window.addEventListener("pagehide", saveCache);

const fingers = new Set();
document.addEventListener("pointerdown", (event) => fingers.add(event.pointerId));
document.addEventListener("pointerup", (event) => {
  if (fingers.size >= 2) {
    const start = gesture._two;
    if (start && event.clientY - start > 48) performUndo();
  }
  fingers.delete(event.pointerId);
  if (fingers.size < 2) gesture._two = null;
});
document.addEventListener("pointerdown", (event) => {
  if (fingers.size >= 2) gesture._two = event.clientY;
});

start();
