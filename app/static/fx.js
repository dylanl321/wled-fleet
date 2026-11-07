// Effect sketches for swatches and for the instant on-screen prediction of a change.
// Matches mock/render.py by effect family. Real pixels from the boards replace the
// prediction within a few frames.
(function () {
  "use strict";

  const STOPS = {
    Party: [[85, 0, 171], [132, 0, 124], [181, 0, 75], [229, 0, 27], [232, 23, 0], [184, 71, 0], [171, 85, 0], [255, 0, 80]],
    Cloud: [[0, 0, 255], [0, 0, 139], [135, 206, 235], [255, 255, 255], [173, 216, 230]],
    Lava: [[0, 0, 0], [128, 0, 0], [255, 0, 0], [255, 165, 0], [255, 255, 255], [255, 165, 0], [255, 0, 0], [128, 0, 0]],
    Ocean: [[25, 25, 112], [0, 0, 139], [0, 0, 128], [0, 0, 205], [46, 139, 87], [0, 128, 128], [95, 158, 160], [0, 0, 255], [0, 139, 139], [100, 149, 237], [127, 255, 212], [135, 206, 235]],
    Forest: [[0, 100, 0], [0, 100, 0], [85, 107, 47], [0, 100, 0], [0, 128, 0], [34, 139, 34], [107, 142, 35], [0, 128, 0], [46, 139, 87], [102, 205, 170], [50, 205, 50], [154, 205, 50]],
    Rainbow: [[255, 0, 0], [171, 85, 0], [171, 171, 0], [0, 255, 0], [0, 171, 85], [0, 0, 255], [85, 0, 171], [171, 0, 85]],
    Sunset: [[120, 0, 0], [179, 22, 0], [255, 104, 0], [167, 22, 18], [100, 0, 103], [16, 0, 130], [0, 0, 160]],
    Fire: [[0, 0, 0], [18, 0, 0], [113, 0, 0], [142, 3, 1], [175, 17, 1], [213, 44, 2], [255, 82, 4], [255, 156, 21], [255, 255, 255]],
    Icefire: [[0, 0, 0], [0, 51, 255], [255, 255, 255], [255, 255, 255], [255, 51, 0], [0, 0, 0]],
    Aurora: [[1, 5, 45], [0, 200, 23], [0, 255, 0], [0, 243, 45], [0, 135, 7], [1, 5, 45]],
    "Aurora 2": [[17, 177, 13], [121, 242, 5], [25, 173, 121], [250, 77, 127], [171, 101, 221]],
    Pastel: [[61, 135, 184], [129, 188, 169], [255, 237, 167], [255, 198, 144], [255, 123, 98]],
    Beach: [[255, 252, 214], [255, 252, 214], [255, 252, 214], [190, 191, 115], [137, 141, 52], [112, 255, 205], [51, 246, 214], [17, 235, 226], [2, 193, 199]],
    Autumn: [[26, 1, 1], [67, 4, 1], [118, 14, 1], [137, 152, 52], [113, 65, 1], [133, 149, 59], [137, 152, 52], [113, 65, 1], [139, 154, 46], [113, 13, 1], [55, 3, 1], [17, 1, 1]],
    "Orange & Teal": [[0, 150, 92], [0, 150, 92], [255, 72, 0], [255, 72, 0]],
    C9: [[184, 4, 0], [184, 4, 0], [144, 44, 2], [144, 44, 2], [4, 96, 2], [4, 96, 2], [7, 7, 88], [7, 7, 88]],
    Sakura: [[196, 19, 10], [255, 69, 45], [223, 45, 72], [255, 82, 103], [223, 13, 17]],
    Candy: [[229, 227, 1], [227, 101, 3], [40, 1, 80], [1, 1, 0], [255, 255, 255]],
    Magenta: [[0, 0, 0], [42, 0, 255], [255, 0, 255], [255, 55, 255], [255, 255, 255]],
    Temperature: [[1, 27, 105], [1, 40, 127], [1, 70, 168], [1, 92, 197], [1, 119, 221], [3, 130, 151], [23, 156, 149], [67, 182, 112], [121, 201, 52], [142, 203, 11], [224, 223, 1], [252, 187, 2], [247, 147, 1], [237, 87, 1], [229, 43, 1], [171, 2, 2]],
    "Red & Blue": [[255, 0, 0], [255, 0, 0], [0, 0, 255], [0, 0, 255]],
    "Retro Clown": [[227, 101, 3], [117, 1, 46], [255, 0, 0], [80, 2, 80]],
  };

  const FAMILIES = [
    ["solid pattern", "stripes"], ["solid", "solid"], ["candle", "candle"], ["fire flicker", "candle"], ["fire", "fire"],
    ["aurora", "aurora"], ["pacifica", "ocean"], ["lake", "ocean"], ["flow", "flow"], ["colorwaves", "flow"],
    ["noise", "flow"], ["plasma", "flow"], ["palette", "flow"], ["pride", "flow"], ["rainbow", "rainbow"],
    ["colorloop", "rainbow"], ["twinkle", "twinkle"], ["sparkle", "twinkle"], ["glitter", "twinkle"], ["fairy", "twinkle"],
    ["dissolve", "twinkle"], ["halloween", "eyes"], ["breathe", "breathe"], ["heartbeat", "breathe"], ["fade", "breathe"],
    ["sunrise", "breathe"], ["blink", "blink"], ["strobe", "blink"], ["lightning", "blink"], ["rain", "rain"], ["drip", "rain"],
    ["ripple", "rain"], ["popcorn", "rain"], ["firework", "rain"], ["chase", "chase"], ["theater", "chase"], ["running", "chase"],
    ["railway", "chase"], ["scan", "comet"], ["meteor", "comet"], ["comet", "comet"], ["sinelon", "comet"], ["lighthouse", "comet"],
    ["wipe", "wipe"], ["sweep", "wipe"], ["random colors", "random"], ["dynamic", "random"],
  ];
  const familyCache = new Map();

  function family(name) {
    const key = String(name || "Solid").toLowerCase();
    if (familyCache.has(key)) return familyCache.get(key);
    let found = "flow";
    for (const [needle, kind] of FAMILIES) {
      if (key.includes(needle)) { found = kind; break; }
    }
    familyCache.set(key, found);
    return found;
  }

  function noise(index, slot, seed) {
    let v = (Math.imul(index, 374761393) + Math.imul(slot, 668265263) + Math.imul(seed || 0, 2246822519 | 0)) >>> 0;
    v = Math.imul(v ^ (v >>> 13), 1274126177) >>> 0;
    return ((v ^ (v >>> 16)) & 0xffff) / 65535;
  }

  function smooth(position, seed) {
    const base = Math.floor(position);
    let f = position - base;
    f = f * f * (3 - 2 * f);
    return noise(base, 0, seed) * (1 - f) + noise(base + 1, 0, seed) * f;
  }

  function mix(a, b, t, out) {
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    out[0] = a[0] + (b[0] - a[0]) * t;
    out[1] = a[1] + (b[1] - a[1]) * t;
    out[2] = a[2] + (b[2] - a[2]) * t;
    return out;
  }

  function stopsFor(palette, colors) {
    const named = STOPS[palette];
    if (palette === "* Color 1") return [colors[0], colors[0]];
    if (named && !String(palette).startsWith("*")) return named;
    const lit = colors.filter((c) => c[0] + c[1] + c[2] > 0);
    if (lit.length > 1) return lit;
    const one = lit[0] || colors[0];
    return [one, [one[0] * 0.35, one[1] * 0.35, one[2] * 0.35]];
  }

  function paletteAt(stops, position, out) {
    position = ((position % 1) + 1) % 1;
    const scaled = position * stops.length;
    const index = Math.floor(scaled) % stops.length;
    return mix(stops[index], stops[(index + 1) % stops.length], scaled - Math.floor(scaled), out);
  }

  function hue(h, out) {
    h = ((h % 1) + 1) % 1;
    const i = Math.floor(h * 6);
    const f = h * 6 - i;
    const q = 1 - f;
    const table = [[1, f, 0], [q, 1, 0], [0, 1, f], [0, q, 1], [f, 0, 1], [1, 0, q]][i % 6];
    out[0] = table[0] * 255; out[1] = table[1] * 255; out[2] = table[2] * 255;
    return out;
  }

  // Returns RGB triples (0..255 floats) for `count` pixels at time `now` (seconds).
  function render(look, count, now, out) {
    const effect = look.fx || "Solid";
    const palette = look.pal || "Default";
    const colors = (look.col && look.col.length ? look.col : [[255, 160, 60]]).map((c) => [c[0] || 0, c[1] || 0, c[2] || 0]);
    while (colors.length < 3) colors.push([0, 0, 0]);
    const primary = colors[0];
    const secondary = colors[1];
    const speed = look.sx == null ? 128 : look.sx;
    const intensity = look.ix == null ? 128 : look.ix;
    const kind = family(effect);
    const clock = now * (0.25 + speed / 110);
    const width = Math.max(1, count);
    const px = out && out.length === width * 3 ? out : new Float32Array(width * 3);
    const tmp = [0, 0, 0];
    const stops = stopsFor(palette, colors);
    for (let i = 0; i < width; i++) {
      const phase = i / width;
      let r = primary[0], g = primary[1], b = primary[2];
      let k = 1;
      switch (kind) {
        case "solid": break;
        case "stripes": {
          const c = Math.floor(i / (Math.floor(intensity / 40) + 1)) % 2 === 0 ? primary : secondary;
          r = c[0]; g = c[1]; b = c[2]; break;
        }
        case "candle": k = 0.55 + 0.45 * smooth(now * (3 + speed / 40) + i * 0.6, i % 7); break;
        case "fire": {
          const heat = smooth(now * (2 + speed / 50) + i * 0.35, 3) * (1 - phase * 0.5);
          paletteAt(STOPS[palette] || STOPS.Fire, heat * 0.85, tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        }
        case "aurora": {
          const wave = smooth(i * 0.08 + clock * 0.4, 11) * 0.6 + 0.2 * Math.sin(phase * 6 + clock);
          paletteAt(STOPS[palette] || STOPS.Aurora, wave + clock * 0.05, tmp);
          k = 0.35 + 0.65 * smooth(i * 0.15 - clock * 0.6, 5); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        }
        case "ocean": {
          const wave = 0.5 + 0.5 * Math.sin(phase * 9 + clock * 1.3) * Math.sin(phase * 3.1 - clock * 0.7);
          paletteAt(STOPS[palette] || STOPS.Ocean, wave * 0.8 + clock * 0.03, tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        }
        case "flow":
          paletteAt(stops, phase + clock * 0.15 + 0.15 * Math.sin(phase * 5 + clock), tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        case "rainbow":
          hue(phase * (0.5 + intensity / 255) + clock * 0.15, tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        case "twinkle": {
          const slot = Math.floor(now * (1 + speed / 40));
          const level = noise(i, slot, 9);
          const density = 0.75 + (255 - intensity) / 1300;
          const base = palette !== "Default" && palette !== "* Color 1" ? paletteAt(stops, noise(i, 0, 2), tmp) : primary;
          if (level > density) { k = (level - density) / (1 - density); r = base[0]; g = base[1]; b = base[2]; }
          else { r = secondary[0] * 0.4; g = secondary[1] * 0.4; b = secondary[2] * 0.4; }
          break;
        }
        case "eyes": {
          const slot = Math.floor(now * 0.8);
          const start = Math.floor(noise(slot, 1, 4) * Math.max(1, width - 4));
          if (!((i === start || i === start + 3) && noise(slot, 2, 4) > 0.3)) k = 0;
          break;
        }
        case "breathe": k = 0.15 + 0.85 * (0.5 + 0.5 * Math.sin(clock * 2.2)); break;
        case "blink": if (Math.floor(clock * 3) % 2) { r = secondary[0]; g = secondary[1]; b = secondary[2]; } break;
        case "rain": {
          const slot = Math.floor(clock * 6);
          if (noise(i, Math.floor(slot / 3), 6) > 0.92) { paletteAt(stops, noise(i, slot, 1), tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; }
          else { r = secondary[0] * 0.3; g = secondary[1] * 0.3; b = secondary[2] * 0.3; }
          break;
        }
        case "chase": if ((i + Math.floor(clock * 6)) % 3 !== 0) { r = secondary[0]; g = secondary[1]; b = secondary[2]; } break;
        case "comet": {
          const head = (clock * 0.3) % 1;
          const tail = Math.max(0, 1 - (((head - phase) % 1) + 1) % 1 * (6 + intensity / 32));
          mix(secondary, primary, tail, tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        }
        case "wipe": {
          const edge = (clock * 0.25) % 2;
          const on = edge <= 1 ? phase < edge : phase >= edge - 1;
          if (!on) { r = secondary[0]; g = secondary[1]; b = secondary[2]; }
          break;
        }
        case "random": hue(noise(Math.floor(i / 3), Math.floor(clock * 1.5), 8), tmp); r = tmp[0]; g = tmp[1]; b = tmp[2]; break;
        default: break;
      }
      px[i * 3] = r * k; px[i * 3 + 1] = g * k; px[i * 3 + 2] = b * k;
    }
    return px;
  }

  function paletteStops(name, colors) {
    return stopsFor(name, (colors || [[255, 160, 60], [0, 0, 0], [0, 0, 0]]).map((c) => [c[0], c[1], c[2]]));
  }

  window.LumenFx = { render, family, paletteStops, STOPS };
})();
