(function () {
  const root = document.getElementById("app");
  const SWATCHES = [
    ["Warm", 255, 170, 70],
    ["Candle", 255, 120, 30],
    ["White", 255, 255, 255],
    ["Cool", 190, 210, 255],
    ["Red", 230, 40, 40],
    ["Green", 40, 190, 80],
    ["Blue", 50, 90, 255],
    ["Pink", 255, 60, 150],
  ];
  const state = {
    session: { required: false, ok: true },
    devices: [],
    status: null,
    live: null,
    backups: [],
    backup: null,
    diff: null,
    bins: [],
    profiles: [],
    releases: [],
    events: [],
    settings: null,
    route: { name: "devices" },
    sheet: null,
    pending: null,
    edits: null,
    draftColor: { r: 255, g: 170, b: 70 },
    selecting: false,
    selected: {},
    showForgotten: false,
    job: null,
    toast: "",
    error: "",
    hold: false,
    booting: true,
    repo: "",
    installEvent: null,
  };
  let timer = 0;
  let repeat = 0;
  let repeated = false;

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function compact(mac) {
    return String(mac || "").replace(/:/g, "").toLowerCase();
  }

  function deviceBy(mac) {
    const key = compact(mac);
    return state.devices.find((item) => compact(item.mac) === key) || null;
  }

  function parseRoute() {
    const parts = (location.hash || "#/").replace(/^#/, "").split("/").filter(Boolean);
    if (parts[0] === "d" && parts[1]) return { name: "device", mac: parts[1] };
    if (parts[0] === "firmware") return { name: "firmware" };
    if (parts[0] === "events") return { name: "events" };
    if (parts[0] === "settings") return { name: "settings" };
    if (parts[0] === "backup" && parts[2]) return { name: "backup", mac: parts[1], id: parts[2] };
    if (parts[0] === "diff" && parts[2]) return { name: "diff", mac: parts[1], id: parts[2] };
    return { name: "devices" };
  }

  function ago(iso) {
    if (!iso) return "no backup yet";
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return iso;
    const seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + " min ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " hr ago";
    return Math.floor(seconds / 86400) + " days ago";
  }

  function when(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso || "";
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function bytes(size) {
    const n = Number(size) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function reasonLabel(reason) {
    return {
      discover: "Found",
      schedule: "Daily",
      change: "Config change",
      manual: "Manual",
      "pre-update": "Before update",
      "pre-restore": "Before restore",
      "pre-change": "Before LED edit",
    }[reason] || reason;
  }

  async function api(path, options) {
    const opts = options || {};
    const headers = { Accept: "application/json" };
    if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method: opts.method || "GET",
      headers,
      body: opts.body,
      credentials: "same-origin",
    });
    if (response.status === 401) {
      state.session = { required: true, ok: false };
      render({ resetScroll: true });
      throw new Error("Sign in required");
    }
    if (!response.ok) {
      let detail = "Request failed";
      try {
        const body = await response.json();
        detail = Array.isArray(body.detail) ? body.detail.map((item) => item.msg).join(" ") : (body.detail || detail);
      } catch (error) { /* response had no json */ }
      throw new Error(detail);
    }
    const type = response.headers.get("content-type") || "";
    if (type.includes("application/json")) return response.json();
    return response;
  }

  function toast(message) {
    state.toast = message;
    const existing = document.getElementById("toast");
    if (existing) existing.remove();
    const node = document.createElement("div");
    node.id = "toast";
    node.className = "toast";
    node.setAttribute("role", "status");
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(() => { if (state.toast === message) { state.toast = ""; node.remove(); } }, 3200);
  }

  function icon(name) {
    const paths = {
      devices: '<circle cx="8" cy="8" r="2.2"/><circle cx="16" cy="8" r="2.2"/><circle cx="8" cy="16" r="2.2"/><circle cx="16" cy="16" r="2.2"/>',
      firmware: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
      events: '<path d="M5 16c2-6 4-6 6 0s4 6 6 0"/><circle cx="12" cy="12" r="9"/>',
      settings: '<path d="M4 8h10M18 8h2M4 16h2M10 16h10"/><circle cx="16" cy="8" r="2"/><circle cx="8" cy="16" r="2"/>',
    };
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">' + paths[name] + "</svg>";
  }

  function standalone() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }

  function showInstall() {
    if (standalone() || localStorage.getItem("install-dismissed") === "1") return false;
    return state.route.name === "devices" || state.route.name === "settings";
  }

  function canRepaint() {
    if (state.sheet || state.hold || state.selecting) return false;
    const el = document.activeElement;
    return !(el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
  }

  function render(options) {
    const main = document.querySelector("main");
    const y = options && options.resetScroll ? 0 : (main ? main.scrollTop : 0);
    root.innerHTML = shell();
    const next = document.querySelector("main");
    if (next) next.scrollTop = y;
    document.body.classList.toggle("sheet-open", Boolean(state.sheet));
    document.body.classList.toggle("has-dock", state.route.name === "device" && !!(state.live && state.live.state));
  }

  function shell() {
    if (state.session.required && !state.session.ok) return loginView();
    return topbar() + "<main>" + body() + "</main>" + dockHtml() + (state.selecting ? actionBar() : tabbar()) + sheetHtml();
  }

  function topbar() {
    const route = state.route;
    let title = "Devices";
    let back = "";
    if (route.name === "device" || route.name === "backup" || route.name === "diff") {
      const device = deviceBy(route.mac);
      title = device ? device.name : "Controller";
      const href = route.name === "device" ? "#/" : "#/d/" + route.mac;
      back = '<a class="back" href="' + href + '">Back</a>';
    } else if (route.name === "firmware") title = "Firmware";
    else if (route.name === "events") title = "Activity";
    else if (route.name === "settings") title = "Settings";
    const action = route.name === "devices"
      ? '<button class="iconbtn" type="button" data-action="select-toggle">' + (state.selecting ? "Done" : "Select") + "</button>"
      : '<button class="iconbtn" type="button" data-action="refresh">Refresh</button>';
    return '<header class="topbar">' + back + '<span class="mark"></span><h1>' + esc(title) + "</h1>" + action + "</header>";
  }

  function dockHtml() {
    if (state.route.name !== "device" || !state.live || !state.live.state) return "";
    const device = deviceBy(state.route.mac);
    const online = !!(device && device.online && state.live.online);
    const power = !!state.live.state.on;
    const bri = Number(state.live.state.bri || 0);
    const disabled = online ? "" : " disabled";
    return '<div class="dock">' +
      '<div class="readout"><span>Brightness</span><span id="bri-readout">' + Math.round(bri / 255 * 100) + "% · " + bri + "</span></div>" +
      '<div class="slider"><button class="step" type="button" data-repeat data-action="bri-step" data-dir="-1" aria-label="Decrease brightness"' + disabled + ">−</button>" +
      '<input id="bri-range" type="range" min="0" max="255" value="' + bri + '" aria-label="Brightness"' + disabled + ">" +
      '<button class="step" type="button" data-repeat data-action="bri-step" data-dir="1" aria-label="Increase brightness"' + disabled + ">+</button></div>" +
      '<button class="btn ' + (power ? "primary" : "ghost") + '" type="button" data-role="power" data-action="power" aria-pressed="' + power + '"' + disabled + ">" +
      (power ? "Turn off" : "Turn on") + "</button></div>";
  }

  function tabbar() {
    const tabs = [
      ["devices", "#/", "Devices"],
      ["firmware", "#/firmware", "Firmware"],
      ["events", "#/events", "Activity"],
      ["settings", "#/settings", "Settings"],
    ];
    return '<nav class="tabs" aria-label="Primary">' + tabs.map((tab) => {
      const current = state.route.name === tab[0] || (tab[0] === "devices" && ["device", "backup", "diff"].includes(state.route.name));
      return '<a href="' + tab[1] + '"' + (current ? ' aria-current="page"' : "") + ">" + icon(tab[0]) + esc(tab[2]) + "</a>";
    }).join("") + "</nav>";
  }

  function actionBar() {
    const count = Object.keys(state.selected).length;
    return '<div class="actionbar" role="toolbar" aria-label="Selected controllers">' +
      '<button type="button" data-action="bulk-backup">Backup ' + count + "</button>" +
      '<button type="button" data-action="bulk-reboot">Reboot</button>' +
      '<button type="button" data-action="bulk-firmware">Update</button>' +
      '<button type="button" data-action="select-toggle">Cancel</button></div>';
  }

  function body() {
    if (state.booting) return '<p class="boot">Loading controllers…</p>';
    if (state.error) return '<p class="error">' + esc(state.error) + '</p><button class="btn" type="button" data-action="refresh">Try again</button>';
    const views = {
      devices: viewDevices,
      device: viewDevice,
      firmware: viewFirmware,
      events: viewEvents,
      settings: viewSettings,
      backup: viewBackup,
      diff: viewDiff,
    };
    return (views[state.route.name] || viewDevices)();
  }

  function installBanner() {
    if (!showInstall()) return "";
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const button = state.installEvent
      ? '<button class="btn primary" type="button" data-action="install">Install</button>'
      : "";
    const hint = ios
      ? "In Safari, tap Share, then Add to Home Screen. It opens full screen, like an app."
      : "Add WLED Fleet to your home screen so the controls stay under your thumb.";
    return '<section class="banner"><strong>Keep it on your phone</strong><p>' + hint + "</p>" + button +
      '<button class="btn ghost" type="button" data-action="dismiss-install">Not now</button></section>';
  }

  function viewDevices() {
    const visible = state.devices.filter((item) => state.showForgotten || !item.forgotten);
    const online = state.devices.filter((item) => item.online && !item.forgotten).length;
    const scan = state.status && state.status.scan ? ago(state.status.scan.at) : "not yet";
    const forgotten = state.devices.filter((item) => item.forgotten).length;
    let list = "";
    if (!visible.length) {
      list = '<section class="card"><h2>No controllers yet</h2><p class="meta">WLED announces itself on the local network, and this app is listening. A new controller shows up on its own. If one is powered on but missing, add its subnet in Settings.</p>' +
        '<button class="btn primary" type="button" data-action="refresh">Scan again</button></section>';
    } else {
      list = visible.map((device) => {
        const href = "#/d/" + compact(device.mac);
        const checked = state.selected[device.mac] ? "checked" : "";
        const box = state.selecting
          ? '<label class="check"><input type="checkbox" ' + checked + ' data-action="toggle-select" data-mac="' + esc(device.mac) + '"> Select</label>'
          : "";
        const tag = state.selecting ? "div" : "a";
        const attr = state.selecting ? "" : ' href="' + href + '"';
        return "<" + tag + attr + ' class="device">' +
          '<div class="status"><span class="dot ' + (device.online ? "on" : "off") + '"></span>' +
          (device.activity ? esc(device.activity) : (device.online ? "Online" : "Offline")) +
          (device.starred ? " · Marked" : "") + "</div>" +
          '<p class="name">' + esc(device.name) + "</p>" +
          '<p class="meta">' + esc(device.arch || "unknown chip") + " · " + esc(device.version || "unknown") +
          (device.led_count != null ? " · " + device.led_count + " LEDs" : "") + "</p>" +
          '<p class="meta mono">' + esc(device.ip) + (device.port && device.port !== 80 ? ":" + device.port : "") + " · " + esc(device.mac) + "</p>" +
          '<p class="meta">Backup ' + esc(ago(device.last_backup_at)) + (device.rssi != null ? " · " + device.rssi + " dBm" : "") + "</p>" +
          (device.last_error ? '<p class="warn">' + esc(device.last_error) + "</p>" : "") +
          box + "</" + tag + ">";
      }).join("");
    }
    return '<div class="summary"><span>' + online + " online</span><span>Scanned " + esc(scan) + "</span></div>" +
      installBanner() + list +
      (forgotten ? '<button class="btn ghost" type="button" data-action="forgotten-toggle">' +
        (state.showForgotten ? "Hide forgotten" : "Forgotten (" + forgotten + ")") + "</button>" : "");
  }

  function currentSegs(device) {
    if (state.edits && state.edits.mac === compact(device.mac)) return state.edits;
    const live = state.live || {};
    return {
      mac: compact(device.mac),
      buses: (live.buses || []).map((bus) => ({ start: bus.start, len: bus.len, pin: bus.pin })),
      segs: ((live.state && live.state.seg) || []).map((seg) => ({
        id: seg.id || 0,
        start: seg.start || 0,
        stop: seg.stop || 0,
        on: seg.on !== false,
        rev: !!seg.rev,
      })),
    };
  }

  function viewDevice() {
    const device = deviceBy(state.route.mac);
    if (!device) return '<p class="error">That controller is not in the fleet.</p>';
    const live = state.live;
    if (!live) return '<p class="boot">Reading ' + esc(device.name) + "…</p>";
    const online = live.online && device.online;
    const edits = currentSegs(device);
    const color = live.state && live.state.seg && live.state.seg[0] && live.state.seg[0].col ? live.state.seg[0].col[0] : [255, 170, 70];
    const colorKey = color.join(",");
    return (online ? "" : '<p class="error">' + esc(live.error || "This controller is not answering. Backups are still here.") + "</p>") +
      '<p class="meta mono">' + esc(device.ip) + (device.port && device.port !== 80 ? ":" + device.port : "") + " · " + esc(device.mac) + "<br>" +
      esc(device.arch || "") + " · " + esc(device.version || "unknown version") +
      (device.release_name ? " · " + esc(device.release_name) : "") + "</p>" +
      (live.ssid ? '<p class="meta">Wi-Fi ' + esc(live.ssid) + (live.ap_ssid ? " · AP " + esc(live.ap_ssid) : "") + " · not editable here</p>" : "") +
      '<div class="section"><h2>Color</h2><div class="swatches">' + SWATCHES.map((swatch) => {
        return '<button class="swatch' + (colorKey === swatch.slice(1).join(",") ? " on" : "") + '" type="button" data-action="swatch" data-swatch="' +
        swatch.slice(1).join(",") + '" aria-label="' + esc(swatch[0]) + '" style="background:rgb(' + swatch.slice(1).join(",") + ')"' +
        (online ? "" : " disabled") + "></button>";
      }).join("") + "</div>" +
      '<button class="btn ghost" type="button" data-action="custom-color"' + (online ? "" : " disabled") + ">Custom color</button></div>" +
      '<div class="section"><h2>Presets</h2><div class="chips">' + ((live.presets || []).length
        ? live.presets.map((preset) => '<button class="chip' + (live.state && live.state.ps === preset.id ? " on" : "") +
          '" type="button" data-action="preset" data-id="' + preset.id + '"' + (online ? "" : " disabled") + ">" + esc(preset.name) + "</button>").join("")
        : '<p class="meta">No presets stored on this controller.</p>') + "</div></div>" +
      '<div class="grid3"><button class="btn" type="button" data-action="identify"' + (online ? "" : " disabled") + ">Identify</button>" +
      '<button class="btn" type="button" data-action="reboot-ask"' + (online ? "" : " disabled") + ">Reboot</button>" +
      '<button class="btn" type="button" data-action="backup-now">Backup</button></div>' +
      segmentBlock(edits, online) +
      ledBlock(edits, online) +
      renameBlock(device, live) +
      backupBlock(device) +
      '<div class="section"><h2>Firmware</h2><p class="meta">' + esc(device.profile_name || "No profile") +
      (device.board ? " · " + esc(device.board) : "") + "</p>" +
      '<button class="btn primary" type="button" data-action="firmware-open"' + (online ? "" : " disabled") + ">Update firmware</button>" +
      '<button class="btn ghost" type="button" data-action="secrets-open">PIN and OTA passphrase</button></div>' +
      '<button class="btn ghost" type="button" data-action="star">' + (device.starred ? "Unmark" : "Mark") + "</button>" +
      '<button class="btn danger" type="button" data-action="forget-ask">' + (device.forgotten ? "Bring back" : "Forget") + "</button>";
  }

  function segmentBlock(edits, online) {
    if (!edits.segs.length) return "";
    return '<div class="section"><h2>Segments</h2>' + edits.segs.map((seg) =>
      '<div class="card"><strong>Segment ' + Number(seg.id) + '</strong>' +
      stepper("Start", "seg", seg.id, "start", seg.start) +
      stepper("End", "seg", seg.id, "stop", seg.stop) +
      '<button class="btn ghost" type="button" data-action="seg-toggle" data-id="' + seg.id + '" data-field="on">' +
      (seg.on ? "Segment on" : "Segment off") + "</button>" +
      '<button class="btn ghost" type="button" data-action="seg-toggle" data-id="' + seg.id + '" data-field="rev">' +
      (seg.rev ? "Reversed" : "Not reversed") + "</button></div>"
    ).join("") +
      '<button class="btn primary" type="button" data-action="seg-apply"' + (online ? "" : " disabled") + ">Apply segments</button></div>";
  }

  function ledBlock(edits, online) {
    if (!edits.buses.length) return '<div class="section"><h2>LED outputs</h2><p class="meta">No LED outputs in the saved config yet.</p></div>';
    return '<div class="section"><h2>LED outputs</h2><p class="meta">Applying this writes the hardware config and reboots the controller. Wi-Fi stays as it is.</p>' +
      edits.buses.map((bus, index) =>
        '<div class="card"><strong>Output ' + (index + 1) + (bus.pin != null ? " · pin " + bus.pin : "") + "</strong>" +
        stepper("Start", "led", index, "start", bus.start) +
        stepper("Length", "led", index, "len", bus.len) + "</div>"
      ).join("") +
      '<button class="btn primary" type="button" data-action="led-ask"' + (online ? "" : " disabled") + ">Apply LED lengths</button></div>";
  }

  function stepper(label, group, id, field, value) {
    return '<div class="pair"><span>' + label + '</span><div class="stepper">' +
      '<button class="step" type="button" data-repeat data-action="nudge" data-group="' + group + '" data-id="' + id +
      '" data-field="' + field + '" data-dir="-1" aria-label="Decrease ' + label + '">−</button>' +
      '<input inputmode="numeric" aria-label="' + label + '" value="' + Number(value) + '" data-action="typed" data-group="' +
      group + '" data-id="' + id + '" data-field="' + field + '">' +
      '<button class="step" type="button" data-repeat data-action="nudge" data-group="' + group + '" data-id="' + id +
      '" data-field="' + field + '" data-dir="1" aria-label="Increase ' + label + '">+</button></div></div>';
  }

  function renameBlock(device, live) {
    const mdns = (live && live.mdns) || device.mdns || "";
    return '<div class="section"><h2>Name</h2><label class="field"><span>Server description</span>' +
      '<input id="rename-name" type="text" maxlength="32" value="' + esc(device.name) + '"></label>' +
      '<label class="field"><span>mDNS name</span><input id="rename-mdns" type="text" maxlength="32" autocapitalize="off" value="' +
      esc(mdns) + '"></label><p class="meta">Changing the mDNS name reboots the controller. The display name applies immediately.</p>' +
      '<button class="btn primary" type="button" data-action="rename">Save name</button></div>';
  }

  function backupBlock(device) {
    if (!state.backups.length) {
      return '<div class="section"><h2>Backups</h2><p class="meta">A backup is taken as soon as this controller is found, then each day and whenever the config changes.</p></div>';
    }
    return '<div class="section"><h2>Backups</h2>' + state.backups.map((row) =>
      '<article class="card"><strong>' + esc(when(row.created_at)) + "</strong>" +
      '<p class="meta">' + esc(reasonLabel(row.reason)) + " · " + esc(row.version || "") + " · " + bytes(row.byte_size) + "</p>" +
      '<p class="mono">' + esc(row.content_hash.slice(0, 12)) + "</p>" +
      '<div class="grid2"><a class="btn" href="#/backup/' + compact(device.mac) + "/" + row.id + '">View</a>' +
      '<a class="btn" href="#/diff/' + compact(device.mac) + "/" + row.id + '">Diff</a>' +
      '<button class="btn" type="button" data-action="download-backup" data-id="' + row.id + '">Download</button>' +
      '<button class="btn danger" type="button" data-action="restore-ask" data-id="' + row.id + '">Restore</button></div></article>'
    ).join("") + "</div>";
  }

  function viewBackup() {
    const row = state.backup;
    if (!row) return '<p class="boot">Loading backup…</p>';
    const block = (title, value, text) => "<h2>" + title + "</h2><pre>" + esc(text || JSON.stringify(value, null, 2)) + "</pre>";
    return '<p class="meta">' + esc(when(row.created_at)) + " · " + esc(reasonLabel(row.reason)) + "</p>" +
      block("Config", row.cfg, row.cfg_text) + block("Presets", row.presets, row.presets_text) +
      '<button class="btn" type="button" data-action="download-backup" data-id="' + row.id + '">Download zip</button>' +
      '<button class="btn danger" type="button" data-action="restore-ask" data-id="' + row.id + '">Restore onto controller</button>';
  }

  function viewDiff() {
    const diff = state.diff;
    if (!diff) return '<p class="boot">Comparing backups…</p>';
    if (!diff.changes.length) return "<p>These backups match.</p>";
    return (diff.against ? '<p class="meta">Compared with ' + esc(when(diff.against.created_at)) + "</p>" : "") +
      diff.changes.map((change) => '<div class="change"><b>' + esc(change.path) + "</b><div>" +
        esc(showVal(change.before)) + " → " + esc(showVal(change.after)) + "</div></div>").join("") +
      (diff.truncated ? '<p class="meta">Showing the first 400 changes.</p>' : "");
  }

  function showVal(value) {
    if (value == null) return "—";
    const text = typeof value === "object" ? JSON.stringify(value) : String(value);
    return text.length > 180 ? text.slice(0, 180) + "…" : text;
  }

  function viewFirmware() {
    const profiles = state.profiles.map((profile) =>
      '<article class="card"><h2>' + esc(profile.name) + '</h2><p class="meta">' + esc(profile.chip_family) +
      (profile.board ? " · " + esc(profile.board) : "") + '</p><p class="meta">' + esc(profile.notes || "") + "</p>" +
      '<p class="meta">' + (profile.bin_id ? "Firmware file attached" : "No file attached yet") + "</p></article>"
    ).join("");
    const bins = state.bins.map((bin) =>
      '<article class="card"><strong>' + esc(bin.filename) + '</strong><p class="meta">' + esc(bin.chip_family || "unknown chip") +
      " · " + bytes(bin.byte_size) + " · " + esc(bin.source) + "</p>" +
      '<button class="btn danger" type="button" data-action="delete-bin" data-id="' + bin.id + '">Remove file</button></article>'
    ).join("");
    const releases = state.releases.map((release) =>
      "<h2>" + esc(release.tag || release.name) + "</h2>" + release.assets.map((asset) =>
        '<button class="rowbtn" type="button" data-action="download-asset" data-url="' + esc(asset.url) + '" data-name="' + esc(asset.name) + '">' +
        "<strong>" + esc(asset.name) + '</strong><br><span class="meta">' + bytes(asset.size) +
        (asset.board ? " · " + esc(asset.board) : "") + (asset.chip_family ? " · " + esc(asset.chip_family) : "") +
        "</span></button>"
      ).join("")
    ).join("");
    return '<p class="meta">Dig-Uno v3 and v4 need the Dig-Uno file from QuinLED, not a generic ESP32 image. Older ESP8266 boards use the official WLED ESP8266 file. Ethernet and AE+ hats are separate files. The chip is checked from the file before anything is flashed. Nothing updates on its own.</p>' +
      '<button class="btn primary" type="button" data-action="add-profiles">Add Dig-Uno profiles</button>' +
      '<label class="btn filebtn">Upload a .bin<input type="file" accept=".bin,application/octet-stream" data-action="upload-bin"></label>' +
      '<div class="section"><h2>Profiles</h2>' + (profiles || '<p class="meta">No profiles yet.</p>') + "</div>" +
      '<div class="section"><h2>Library</h2>' + (bins || '<p class="meta">No firmware files yet.</p>') + "</div>" +
      '<div class="section"><h2>Releases</h2><div class="grid2">' +
      '<button class="btn" type="button" data-action="load-releases" data-repo="quinled">QuinLED builds</button>' +
      '<button class="btn" type="button" data-action="load-releases" data-repo="wled">Official WLED</button></div>' +
      (releases || '<p class="meta">Load a release list when this host can reach GitHub.</p>') + "</div>";
  }

  function viewEvents() {
    if (!state.events.length) return '<p class="meta">Discovery, backups, and updates will show up here.</p>';
    return state.events.map((event) =>
      '<article class="card"><p class="meta">' + esc(when(event.ts)) + " · " + esc(event.level) + "</p><p>" +
      esc(event.message) + "</p></article>"
    ).join("");
  }

  function viewSettings() {
    const settings = state.settings;
    if (!settings) return '<p class="boot">Loading settings…</p>';
    const hours = settings.backup_interval / 3600;
    const minutes = settings.backup_watch_interval / 60;
    return installBanner() +
      '<p class="meta">mDNS listens on this host. Add a subnet when controllers are on another VLAN, or when the container cannot see multicast. Leave Wi-Fi editing to the controller itself.</p>' +
      field("Subnets", "set-subnets", settings.scan_subnets, "192.168.1.0/24") +
      field("Extra hosts", "set-hosts", settings.extra_hosts, "192.168.1.40") +
      field("Backup every (hours)", "set-hours", formatNumber(hours), "24") +
      field("Look for config changes (minutes)", "set-minutes", formatNumber(minutes), "15") +
      field("Keep this many backups", "set-keep", settings.backup_retention, "30") +
      field("Full scan every (seconds)", "set-discovery", settings.discovery_interval, "60") +
      field("Online check every (seconds)", "set-presence", settings.presence_interval, "20") +
      '<button class="btn primary" type="button" data-action="save-settings">Save</button>' +
      '<button class="btn ghost" type="button" data-action="reset-settings">Use environment defaults</button>' +
      '<p class="meta">Password: ' + (settings.auth_enabled ? "on" : "off, set ADMIN_PASSWORD to require one") +
      ". TLS: " + (settings.tls ? "on" : "off") + ".</p>";
  }

  function formatNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return Number.isInteger(number) ? String(number) : String(Math.round(number * 100) / 100);
  }

  function field(label, id, value, placeholder) {
    return '<label class="field"><span>' + label + '</span><input id="' + id + '" type="text" value="' +
      esc(value ?? "") + '" placeholder="' + esc(placeholder) + '"></label>';
  }

  function loginView() {
    return '<header class="topbar"><span class="mark"></span><h1>WLED Fleet</h1></header><main>' +
      '<p>This fleet is locked with the server password.</p>' +
      '<label class="field"><span>Password</span><input id="password" type="password" autocomplete="current-password"></label>' +
      '<button class="btn primary" type="button" data-action="login">Unlock</button></main>';
  }

  function sheetHtml() {
    const sheet = state.sheet;
    if (!sheet) return "";
    if (sheet.kind === "color") return colorSheet();
    if (sheet.kind === "firmware") return firmwareSheet();
    if (sheet.kind === "secrets") return secretsSheet();
    if (sheet.kind === "results") return resultsSheet();
    return '<button class="backdrop" type="button" data-action="close-sheet" aria-label="Dismiss"></button>' +
      '<div class="sheet" role="dialog" aria-modal="true" aria-label="' + esc(sheet.title) + '"><h2>' + esc(sheet.title) +
      "</h2><p>" + esc(sheet.body || "") + "</p>" +
      (sheet.warnings || []).map((item) => '<p class="warn">' + esc(item) + "</p>").join("") +
      '<button class="btn ' + (sheet.danger ? "danger" : "primary") + '" type="button" data-action="confirm">' + esc(sheet.confirm || "Confirm") +
      '</button><button class="btn ghost" type="button" data-action="close-sheet">Close</button></div>';
  }

  function colorSheet() {
    const color = state.draftColor;
    const hex = "#" + [color.r, color.g, color.b].map((part) => part.toString(16).padStart(2, "0")).join("");
    return '<button class="backdrop" type="button" data-action="close-sheet" aria-label="Dismiss"></button>' +
      '<div class="sheet" role="dialog" aria-modal="true" aria-label="Custom color"><h2>Custom color</h2>' +
      '<div class="preview" style="background:' + hex + '"></div>' +
      '<input type="color" aria-label="Color picker" value="' + hex + '" data-action="color-input">' +
      colorStep("Red", "r") + colorStep("Green", "g") + colorStep("Blue", "b") +
      '<button class="btn primary" type="button" data-action="apply-color">Use this color</button>' +
      '<button class="btn ghost" type="button" data-action="close-sheet">Close</button></div>';
  }

  function colorStep(label, key) {
    return '<div class="pair"><span>' + label + '</span><div class="stepper">' +
      '<button class="step" type="button" data-repeat data-action="color-step" data-key="' + key + '" data-dir="-1" aria-label="Decrease ' + label + '">−</button>' +
      '<input inputmode="numeric" aria-label="' + label + '" value="' + state.draftColor[key] + '" data-key="' + key + '" data-action="color-typed">' +
      '<button class="step" type="button" data-repeat data-action="color-step" data-key="' + key + '" data-dir="1" aria-label="Increase ' + label + '">+</button></div></div>';
  }

  function firmwareSheet() {
    const report = (state.pending && state.pending.report) || { bins: [] };
    const rows = (report.bins || []).map((bin) =>
      '<button class="rowbtn" type="button" data-action="pick-bin" data-id="' + bin.id + '"' + (bin.ok ? "" : " disabled") + ">" +
      "<strong>" + esc(bin.filename) + (bin.profile_bin ? " · profile" : "") + "</strong><br><span class='meta'>" +
      esc(bin.ok ? (bin.warnings && bin.warnings[0]) || bin.reason : bin.reason) + "</span></button>"
    ).join("");
    return '<button class="backdrop" type="button" data-action="close-sheet" aria-label="Dismiss"></button>' +
      '<div class="sheet" role="dialog" aria-modal="true" aria-label="Choose firmware"><h2>Choose firmware</h2>' +
      (rows || '<p>Add a .bin on the Firmware tab first.</p><a class="btn" href="#/firmware">Open firmware</a>') +
      '<button class="btn ghost" type="button" data-action="close-sheet">Close</button></div>';
  }

  function secretsSheet() {
    const device = deviceBy(state.route.mac) || {};
    return '<button class="backdrop" type="button" data-action="close-sheet" aria-label="Dismiss"></button>' +
      '<div class="sheet" role="dialog" aria-modal="true" aria-label="Controller lock"><h2>Controller lock</h2>' +
      "<p class='meta'>Saved only on this server. " + (device.has_pin ? "A PIN is saved. " : "") +
      (device.has_ota_passphrase ? "An OTA passphrase is saved." : "") + "</p>" +
      '<label class="field"><span>Settings PIN</span><input id="secret-pin" type="text" inputmode="numeric" maxlength="8"></label>' +
      '<label class="field"><span>OTA passphrase</span><input id="secret-ota" type="text" maxlength="64"></label>' +
      '<button class="btn primary" type="button" data-action="secrets-save">Save</button>' +
      '<button class="btn ghost" type="button" data-action="secrets-clear">Clear saved secrets</button>' +
      '<button class="btn ghost" type="button" data-action="close-sheet">Close</button></div>';
  }

  function resultsSheet() {
    const rows = (state.pending && state.pending.results) || [];
    return '<button class="backdrop" type="button" data-action="close-sheet" aria-label="Dismiss"></button>' +
      '<div class="sheet" role="dialog" aria-modal="true" aria-label="Results"><h2>Results</h2>' +
      rows.map((row) => '<p>' + esc(row.name || row.mac) + ": " + (row.ok ? "done" : esc(row.error || "failed")) + "</p>").join("") +
      '<button class="btn" type="button" data-action="close-sheet">Close</button></div>';
  }

  async function load(resetScroll) {
    state.route = parseRoute();
    try {
      if (state.session.required && !state.session.ok) {
        state.booting = false;
        render({ resetScroll: true });
        return;
      }
      state.devices = await api("/api/devices");
      state.status = await api("/api/status");
      const route = state.route;
      if (route.name === "device" && !state.hold) {
        state.live = await api("/api/devices/" + route.mac + "/live");
        state.backups = await api("/api/devices/" + route.mac + "/backups");
      }
      if (route.name === "backup") state.backup = await api("/api/backups/" + route.id);
      if (route.name === "diff") state.diff = await api("/api/backups/" + route.id + "/diff");
      if (route.name === "firmware") {
        state.bins = await api("/api/firmware/bins");
        state.profiles = await api("/api/firmware/profiles");
      }
      if (route.name === "events") state.events = await api("/api/events");
      if (route.name === "settings") state.settings = await api("/api/settings");
      state.error = "";
    } catch (error) {
      if (error.message !== "Sign in required") state.error = error.message;
    }
    state.booting = false;
    if (resetScroll || canRepaint()) render({ resetScroll: !!resetScroll });
  }

  async function refresh() {
    await load(false);
  }

  function ensureEdits(device) {
    if (!state.edits || state.edits.mac !== compact(device.mac)) {
      const base = currentSegs(device);
      state.edits = { mac: compact(device.mac), buses: base.buses.map((bus) => ({ ...bus })), segs: base.segs.map((seg) => ({ ...seg })) };
    }
    return state.edits;
  }

  function nudgeEdit(group, id, field, delta) {
    const device = deviceBy(state.route.mac);
    if (!device) return;
    const edits = ensureEdits(device);
    const list = group === "led" ? edits.buses : edits.segs;
    const item = list[group === "led" ? Number(id) : list.findIndex((seg) => String(seg.id) === String(id))];
    if (!item) return;
    const max = field === "len" || field === "stop" || field === "start" ? 8192 : 8192;
    item[field] = Math.max(0, Math.min(max, Number(item[field] || 0) + delta));
    render();
  }

  function selectedMacs() {
    return Object.keys(state.selected);
  }

  async function run(button, label, work) {
    const previous = button ? button.textContent : "";
    if (button) { button.disabled = true; button.textContent = label; }
    try {
      await work();
    } catch (error) {
      if (error.message !== "Sign in required") toast(error.message);
    } finally {
      if (button && button.isConnected) {
        button.disabled = false;
        if (button.textContent === label) button.textContent = previous;
      }
    }
  }

  async function postState(body) {
    const mac = state.route.mac;
    const next = await api("/api/devices/" + mac + "/state", { method: "POST", body: JSON.stringify(body) });
    if (state.live) state.live.state = next.state || next;
    const power = document.querySelector("[data-role=power]");
    const readout = document.getElementById("bri-readout");
    const range = document.getElementById("bri-range");
    const on = !!(state.live && state.live.state && state.live.state.on);
    const bri = state.live && state.live.state ? Number(state.live.state.bri || 0) : 0;
    if (power) {
      power.textContent = on ? "Turn off" : "Turn on";
      power.classList.toggle("primary", on);
      power.classList.toggle("ghost", !on);
      power.setAttribute("aria-pressed", on ? "true" : "false");
    }
    if (readout) readout.textContent = Math.round(bri / 255 * 100) + "% · " + bri;
    if (range && document.activeElement !== range) range.value = String(bri);
  }

  async function confirmPending() {
    const pending = state.pending || {};
    state.sheet = null;
    render();
    if (pending.type === "reboot") {
      await api("/api/devices/" + pending.mac + "/reboot", { method: "POST" });
      toast("Rebooted");
    } else if (pending.type === "forget") {
      await api("/api/devices/" + pending.mac + "/forget", { method: "POST", body: JSON.stringify({ on: !pending.forgotten }) });
    } else if (pending.type === "restore") {
      const job = await api("/api/backups/" + pending.id + "/restore", { method: "POST" });
      await watchJob(job);
    } else if (pending.type === "leds") {
      await api("/api/devices/" + pending.mac + "/leds", { method: "POST", body: JSON.stringify({ buses: pending.buses }) });
      state.edits = null;
      toast("LED lengths saved");
    } else if (pending.type === "firmware") {
      const job = await api("/api/bulk/firmware", {
        method: "POST",
        body: JSON.stringify({ macs: pending.macs, bin_id: pending.binId, force: false }),
      });
      await watchJob(job);
    } else if (pending.type === "bulk-reboot") {
      await watchJob(await api("/api/bulk/reboot", { method: "POST", body: JSON.stringify({ macs: pending.macs }) }));
    } else if (pending.type === "bulk-backup") {
      await watchJob(await api("/api/bulk/backup", { method: "POST", body: JSON.stringify({ macs: pending.macs }) }));
    }
    await load(false);
  }

  async function watchJob(job) {
    state.job = job;
    sessionStorage.setItem("fleet-job", job.id);
    while (job && (job.status === "queued" || job.status === "running")) {
      await new Promise((resolve) => setTimeout(resolve, 800));
      job = await api("/api/jobs/" + job.id);
      state.job = job;
    }
    sessionStorage.removeItem("fleet-job");
    const failed = (job.results || []).filter((row) => !row.ok);
    if (failed.length) {
      state.pending = { results: job.results };
      state.sheet = { kind: "results" };
    } else {
      toast("Done");
    }
    state.job = null;
  }

  async function onClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target || target.dataset.repeat) return;
    const action = target.dataset.action;
    if (action === "close-sheet") {
      state.sheet = null;
      render();
      return;
    }
    if (action === "confirm") {
      await run(target, "Working…", confirmPending);
      return;
    }
    if (action === "refresh" || action === "scan") {
      await run(target, "Scanning…", async () => {
        await api("/api/scan", { method: "POST" });
        await load(false);
      });
      return;
    }
    if (action === "select-toggle") {
      state.selecting = !state.selecting;
      if (!state.selecting) state.selected = {};
      render();
      return;
    }
    if (action === "forgotten-toggle") {
      state.showForgotten = !state.showForgotten;
      render();
      return;
    }
    if (action === "login") {
      await run(target, "Checking…", async () => {
        await api("/api/login", { method: "POST", body: JSON.stringify({ password: document.getElementById("password").value }) });
        state.session = { required: true, ok: true };
        await load(true);
      });
      return;
    }
    if (action === "power") {
      const on = target.getAttribute("aria-pressed") === "true";
      await run(target, on ? "Turning off…" : "Turning on…", () => postState({ on: !on }));
      return;
    }
    if (action === "swatch") {
      const parts = target.dataset.swatch.split(",").map(Number);
      await run(target, "…", async () => {
        const next = await api("/api/devices/" + state.route.mac + "/color", {
          method: "POST",
          body: JSON.stringify({ r: parts[0], g: parts[1], b: parts[2], segment: 0, on: true }),
        });
        if (state.live) state.live.state = next.state || next;
        render();
      });
      return;
    }
    if (action === "custom-color") {
      const current = state.live && state.live.state && state.live.state.seg && state.live.state.seg[0].col;
      if (current && current[0]) state.draftColor = { r: current[0][0], g: current[0][1], b: current[0][2] };
      state.sheet = { kind: "color" };
      render();
      return;
    }
    if (action === "apply-color") {
      const color = state.draftColor;
      await run(target, "Setting…", async () => {
        const next = await api("/api/devices/" + state.route.mac + "/color", {
          method: "POST",
          body: JSON.stringify({ r: color.r, g: color.g, b: color.b, segment: 0, on: true }),
        });
        if (state.live) state.live.state = next.state || next;
        state.sheet = null;
      });
      await load(false);
      return;
    }
    if (action === "preset") {
      await run(target, "Applying…", () => postState({ ps: Number(target.dataset.id) }));
      render();
      return;
    }
    if (action === "identify") {
      await run(target, "Flashing…", () => api("/api/devices/" + state.route.mac + "/identify", { method: "POST" }));
      return;
    }
    if (action === "backup-now") {
      await run(target, "Saving…", async () => {
        await api("/api/devices/" + state.route.mac + "/backup", { method: "POST" });
        toast("Backup saved");
        await load(false);
      });
      return;
    }
    if (action === "star") {
      const device = deviceBy(state.route.mac);
      await api("/api/devices/" + state.route.mac + "/star", { method: "POST", body: JSON.stringify({ on: !device.starred }) });
      await load(false);
      return;
    }
    if (action === "reboot-ask") {
      const device = deviceBy(state.route.mac);
      state.pending = { type: "reboot", mac: device.mac };
      state.sheet = { kind: "confirm", title: "Reboot " + device.name + "?", body: "The lights go out for a few seconds.", confirm: "Reboot" };
      render();
      return;
    }
    if (action === "forget-ask") {
      const device = deviceBy(state.route.mac);
      state.pending = { type: "forget", mac: device.mac, forgotten: device.forgotten };
      state.sheet = device.forgotten
        ? { kind: "confirm", title: "Bring back " + device.name + "?", body: "Backups and checks start again.", confirm: "Bring back" }
        : { kind: "confirm", title: "Forget " + device.name + "?", body: "It stays in the forgotten list and keeps its backups. It will not be updated or backed up.", confirm: "Forget", danger: true };
      render();
      return;
    }
    if (action === "seg-toggle") {
      const device = deviceBy(state.route.mac);
      const edits = ensureEdits(device);
      const seg = edits.segs.find((item) => String(item.id) === target.dataset.id);
      if (seg) seg[target.dataset.field] = !seg[target.dataset.field];
      render();
      return;
    }
    if (action === "seg-apply") {
      const device = deviceBy(state.route.mac);
      const edits = ensureEdits(device);
      await run(target, "Applying…", async () => {
        await api("/api/devices/" + state.route.mac + "/segments", { method: "POST", body: JSON.stringify({ segments: edits.segs }) });
        state.edits = null;
        toast("Segments updated");
        await load(false);
      });
      return;
    }
    if (action === "led-ask") {
      const device = deviceBy(state.route.mac);
      const edits = ensureEdits(device);
      state.pending = { type: "leds", mac: device.mac, buses: edits.buses.map((bus) => ({ start: Number(bus.start), len: Number(bus.len) })) };
      state.sheet = { kind: "confirm", title: "Apply LED lengths?", body: "The controller reboots. Wi-Fi settings are left alone.", confirm: "Apply and reboot" };
      render();
      return;
    }
    if (action === "rename") {
      await run(target, "Saving…", async () => {
        await api("/api/devices/" + state.route.mac + "/rename", {
          method: "POST",
          body: JSON.stringify({
            name: document.getElementById("rename-name").value,
            mdns: document.getElementById("rename-mdns").value,
          }),
        });
        toast("Name saved");
        await load(false);
      });
      return;
    }
    if (action === "restore-ask") {
      state.pending = { type: "restore", id: Number(target.dataset.id) };
      state.sheet = {
        kind: "confirm",
        title: "Restore this backup?",
        body: "The current config is backed up first, then the controller reboots.",
        confirm: "Restore",
        danger: true,
      };
      render();
      return;
    }
    if (action === "download-backup") {
      await run(target, "Preparing…", async () => {
        const response = await fetch("/api/backups/" + target.dataset.id + "/download");
        if (!response.ok) throw new Error("Download failed");
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "wled-backup-" + target.dataset.id + ".zip";
        link.click();
        URL.revokeObjectURL(url);
      });
      return;
    }
    if (action === "firmware-open" || action === "bulk-firmware") {
      const macs = action === "bulk-firmware" ? selectedMacs() : [deviceBy(state.route.mac).mac];
      if (!macs.length) { toast("Select a controller"); return; }
      await run(target, "Checking…", async () => {
        state.bins = await api("/api/firmware/bins");
        const report = await api("/api/firmware/check", { method: "POST", body: JSON.stringify({ macs, bin_id: null }) });
        state.pending = { type: "firmware", macs, report: mergeReports(report.devices) };
        state.sheet = { kind: "firmware" };
        render();
      });
      return;
    }
    if (action === "pick-bin") {
      const pending = state.pending;
      const bin = (pending.report.bins || []).find((item) => String(item.id) === target.dataset.id);
      pending.binId = Number(target.dataset.id);
      state.sheet = {
        kind: "confirm",
        title: "Update " + pending.macs.length + " controller" + (pending.macs.length > 1 ? "s" : "") + "?",
        body: "A backup runs first. The controller reboots and is checked afterwards.",
        confirm: "Update",
        warnings: bin ? bin.warnings : [],
      };
      render();
      return;
    }
    if (action === "bulk-backup" || action === "bulk-reboot") {
      const macs = selectedMacs();
      if (!macs.length) { toast("Select a controller"); return; }
      state.pending = { type: action, macs };
      state.sheet = {
        kind: "confirm",
        title: action === "bulk-backup" ? "Back up " + macs.length + " controllers?" : "Reboot " + macs.length + " controllers?",
        body: action === "bulk-reboot" ? "They go offline for a few seconds, one after another." : "Identical configs are not stored twice.",
        confirm: action === "bulk-backup" ? "Back up" : "Reboot",
      };
      render();
      return;
    }
    if (action === "add-profiles") {
      await run(target, "Adding…", async () => {
        state.profiles = await api("/api/firmware/profiles/defaults", { method: "POST" });
        toast("Profiles added");
        render();
      });
      return;
    }
    if (action === "delete-bin") {
      await run(target, "Removing…", async () => {
        await api("/api/firmware/bins/" + target.dataset.id, { method: "DELETE" });
        await load(false);
      });
      return;
    }
    if (action === "load-releases") {
      const settings = state.settings || await api("/api/settings");
      const repo = target.dataset.repo === "wled" ? settings.wled_github_repo : settings.quinled_github_repo;
      await run(target, "Loading…", async () => {
        const payload = await api("/api/firmware/releases?repo=" + encodeURIComponent(repo));
        state.releases = payload.releases;
        render();
      });
      return;
    }
    if (action === "download-asset") {
      await run(target, "Downloading…", async () => {
        await api("/api/firmware/fetch", {
          method: "POST",
          body: JSON.stringify({ url: target.dataset.url, filename: target.dataset.name }),
        });
        toast("Saved to the library");
        state.bins = await api("/api/firmware/bins");
        render();
      });
      return;
    }
    if (action === "save-settings") {
      await run(target, "Saving…", async () => {
        state.settings = await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({
            scan_subnets: value("set-subnets"),
            extra_hosts: value("set-hosts"),
            backup_interval: Number(value("set-hours")) * 3600,
            backup_watch_interval: Number(value("set-minutes")) * 60,
            backup_retention: Number(value("set-keep")),
            discovery_interval: Number(value("set-discovery")),
            presence_interval: Number(value("set-presence")),
          }),
        });
        toast("Saved");
        render();
      });
      return;
    }
    if (action === "reset-settings") {
      state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({ reset: true }) });
      toast("Using environment defaults");
      render();
      return;
    }
    if (action === "secrets-open") {
      state.sheet = { kind: "secrets" };
      render();
      return;
    }
    if (action === "secrets-save" || action === "secrets-clear") {
      const body = action === "secrets-clear"
        ? { clear_pin: true, clear_ota: true }
        : { pin: value("secret-pin"), ota_passphrase: value("secret-ota") };
      await run(target, "Saving…", async () => {
        await api("/api/devices/" + state.route.mac + "/secrets", { method: "POST", body: JSON.stringify(body) });
        state.sheet = null;
        toast("Saved");
        await load(false);
      });
      return;
    }
    if (action === "dismiss-install") {
      localStorage.setItem("install-dismissed", "1");
      render();
      return;
    }
    if (action === "install" && state.installEvent) {
      state.installEvent.prompt();
      state.installEvent = null;
      render();
    }
  }

  function value(id) {
    const node = document.getElementById(id);
    return node ? node.value.trim() : "";
  }

  function mergeReports(devices) {
    const first = devices[0] || { bins: [] };
    if (devices.length < 2) return first;
    const bins = first.bins.map((bin) => {
      const problems = devices.map((device) => {
        const match = (device.bins || []).find((item) => item.id === bin.id);
        return match && match.ok ? null : (device.name + ": " + ((match && match.reason) || "not compatible"));
      }).filter(Boolean);
      return { ...bin, ok: problems.length === 0, reason: problems[0] || bin.reason, warnings: bin.warnings || [] };
    });
    return { bins };
  }

  function onChange(event) {
    const target = event.target;
    if (target.dataset.action === "upload-bin" && target.files && target.files[0]) {
      uploadBin(target.files[0], null);
      target.value = "";
    }
    if (target.dataset.action === "toggle-select") {
      if (target.checked) state.selected[target.dataset.mac] = true;
      else delete state.selected[target.dataset.mac];
      const count = document.querySelector(".actionbar button");
      if (count) count.textContent = "Backup " + Object.keys(state.selected).length;
    }
    if (target.id === "bri-range") {
      state.hold = false;
      postState({ bri: Number(target.value) }).catch((error) => toast(error.message));
    }
    if (target.dataset.action === "typed") {
      const device = deviceBy(state.route.mac);
      if (!device) return;
      const edits = ensureEdits(device);
      const list = target.dataset.group === "led" ? edits.buses : edits.segs;
      const item = target.dataset.group === "led"
        ? list[Number(target.dataset.id)]
        : list.find((seg) => String(seg.id) === target.dataset.id);
      if (item) item[target.dataset.field] = Number(target.value || 0);
    }
    if (target.dataset.action === "color-input") {
      const hex = target.value.replace("#", "");
      state.draftColor = { r: parseInt(hex.slice(0, 2), 16), g: parseInt(hex.slice(2, 4), 16), b: parseInt(hex.slice(4, 6), 16) };
      render();
    }
    if (target.dataset.action === "color-typed") {
      state.draftColor[target.dataset.key] = clamp(target.value);
      render();
    }
  }

  function onInput(event) {
    if (event.target.id === "bri-range") {
      state.hold = true;
      const readout = document.getElementById("bri-readout");
      const value = Number(event.target.value);
      if (readout) readout.textContent = Math.round(value / 255 * 100) + "% · " + value;
      const now = Date.now();
      if (!state._briAt || now - state._briAt > 160) {
        state._briAt = now;
        postState({ bri: value }).catch(() => {});
      }
    }
  }

  function clamp(value) {
    return Math.max(0, Math.min(255, Number(value) || 0));
  }

  async function uploadBin(file, family) {
    const body = new FormData();
    body.append("file", file);
    if (family) body.append("chip_family", family);
    try {
      await api("/api/firmware/bins", { method: "POST", body });
      toast("Firmware stored");
      if (state.route.name === "firmware") await load(false);
    } catch (error) {
      toast(error.message);
    }
  }

  function startRepeat(button) {
    const fire = () => {
      repeated = true;
      if (button.dataset.action === "bri-step") {
        const range = document.getElementById("bri-range");
        if (!range) return;
        range.value = String(Math.max(0, Math.min(255, Number(range.value) + Number(button.dataset.dir) * 15)));
        range.dispatchEvent(new Event("input", { bubbles: true }));
        range.dispatchEvent(new Event("change", { bubbles: true }));
      } else if (button.dataset.action === "nudge") {
        nudgeEdit(button.dataset.group, button.dataset.id, button.dataset.field, Number(button.dataset.dir));
      } else if (button.dataset.action === "color-step") {
        state.draftColor[button.dataset.key] = clamp(state.draftColor[button.dataset.key] + Number(button.dataset.dir) * 5);
        render();
      }
    };
    fire();
    repeat = setInterval(fire, 90);
  }

  root.addEventListener("click", (event) => {
    const repeatButton = event.target.closest("[data-repeat]");
    if (repeatButton) {
      if (repeated) {
        repeated = false;
        return;
      }
      if (repeatButton.dataset.action === "bri-step") {
        const range = document.getElementById("bri-range");
        if (range) {
          range.value = String(Math.max(0, Math.min(255, Number(range.value) + Number(repeatButton.dataset.dir) * 15)));
          range.dispatchEvent(new Event("input", { bubbles: true }));
          range.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } else if (repeatButton.dataset.action === "nudge") {
        nudgeEdit(repeatButton.dataset.group, repeatButton.dataset.id, repeatButton.dataset.field, Number(repeatButton.dataset.dir));
      } else if (repeatButton.dataset.action === "color-step") {
        state.draftColor[repeatButton.dataset.key] = clamp(Number(state.draftColor[repeatButton.dataset.key]) + Number(repeatButton.dataset.dir) * 5);
        render();
      }
      return;
    }
    onClick(event);
  });
  root.addEventListener("change", onChange);
  root.addEventListener("input", onInput);
  root.addEventListener("pointerdown", (event) => {
    const button = event.target.closest("[data-repeat]");
    if (!button || button.disabled) return;
    event.preventDefault();
    repeated = false;
    startRepeat(button);
  });
  window.addEventListener("pointerup", () => { clearInterval(repeat); repeat = 0; });
  window.addEventListener("pointercancel", () => { clearInterval(repeat); repeat = 0; });
  window.addEventListener("hashchange", () => {
    state.sheet = null;
    state.edits = null;
    state.selecting = false;
    state.selected = {};
    load(true);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installEvent = event;
    if (canRepaint()) render();
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  async function boot() {
    try {
      state.session = await api("/api/session");
    } catch (error) {
      state.session = { required: false, ok: true };
    }
    const saved = sessionStorage.getItem("fleet-job");
    await load(true);
    if (saved && state.session.ok) {
      try { await watchJob(await api("/api/jobs/" + saved)); await load(false); } catch (error) { sessionStorage.removeItem("fleet-job"); }
    }
    const tick = async () => {
      if (!document.hidden) await refresh();
      timer = setTimeout(tick, state.job ? 1000 : 5000);
    };
    timer = setTimeout(tick, 5000);
  }

  let touchX = null;
  root.addEventListener("touchstart", (event) => {
    if (event.touches.length !== 1) return;
    touchX = event.touches[0].clientX;
  }, { passive: true });
  root.addEventListener("touchend", (event) => {
    if (touchX == null || touchX > 24) { touchX = null; return; }
    const end = event.changedTouches[0].clientX;
    if (end - touchX > 80 && state.route.name !== "devices") history.back();
    touchX = null;
  }, { passive: true });

  boot();
})();
