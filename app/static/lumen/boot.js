/* Paint the last screen before the module loads, so relaunch is not a blank frame. */
(function boot() {
  var raw;
  try {
    raw = localStorage.getItem("lumen.boot");
  } catch (error) {
    return;
  }
  if (!raw) return;
  var data;
  try {
    data = JSON.parse(raw);
  } catch (error) {
    return;
  }
  var app = document.getElementById("app");
  if (!app || !data) return;
  window.__lumenCache = data;
  var route = data.screen || "home";
  document.body.dataset.route = route;
  app.dataset.booted = "cache";
  app.innerHTML = markup(data, route);
  var scroller = app.querySelector('[data-screen="' + route + '"]');
  if (scroller && data.scroll) scroller.scrollTop = data.scroll;
})();

function markup(data, route) {
  var status = statusBar(data);
  if (route === "find") return status + '<div class="stage">' + screen("find", true, findStub(data)) + "</div>";
  if (route === "maintenance") return status + '<div class="stage">' + screen("maintenance", true, "<p class='meta' style='margin:16px'>Maintenance</p>") + "</div>";
  return status + '<div class="stage">' + screen("home", true, homeStub(data)) + "</div>" + sheetStub(data);
}

function statusBar(data) {
  return '<header class="status" data-status-strip><button class="property" data-property>' + escapeHtml(data.property || "All lights") + "</button></header>";
}

function screen(name, active, body) {
  return '<section class="screen" data-screen="' + name + '"' + (active ? ' data-active="true"' : "") + ">" + body + "</section>";
}

function homeStub(data) {
  var lights = data.lights || [];
  var zones = data.zones || [];
  var collapsed = data.collapsed || [];
  var html = '<div class="home-scroll">';
  var grouped = {};
  zones.forEach(function (zone) {
    grouped[zone.id] = [];
  });
  var other = [];
  lights.forEach(function (light) {
    if (light.zone_id && grouped[light.zone_id]) grouped[light.zone_id].push(light);
    else other.push(light);
  });
  zones.forEach(function (zone) {
    html += zoneBlock(zone, grouped[zone.id] || [], collapsed.indexOf(zone.id) !== -1);
  });
  if (other.length) html += zoneBlock({ id: "", name: "Other" }, other, false);
  html += "</div>";
  return html;
}

function zoneBlock(zone, lights, collapsed) {
  var html = '<section class="zone"><header class="zone-head"><span class="zone-name">' + escapeHtml(zone.name) + "</span></header>";
  if (!collapsed) {
    lights.forEach(function (light) {
      html += ribbon(light);
    });
  }
  return html + "</section>";
}

function ribbon(light) {
  var pct = Math.round(((light.bri == null ? 128 : light.bri) / 255) * 100);
  return '<article class="light' + (light.on === false ? " is-off" : "") + '" data-light="' + light.id + '"><canvas class="ribbon" data-ribbon></canvas><span class="light-name" data-light-name="' + escapeHtml(light.name) + '">' + escapeHtml(light.name) + '</span><span class="pct">' + pct + "%</span></article>";
}

function sheetStub(data) {
  if (data.sheet !== "paint") return "";
  return '<section class="sheet" data-paint data-open="true"><div class="grab"></div></section>';
}

function findStub(data) {
  return '<p class="meta" style="margin:16px">Find</p>';
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
