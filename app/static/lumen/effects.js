/** Draw a Look from its effect name, palette, and colors. Brightness is separate. */

const PALETTES = {
  default: [[255, 214, 170], [255, 160, 60]],
  fire: [[255, 80, 0], [255, 180, 40], [80, 0, 0]],
  ocean: [[0, 40, 120], [0, 140, 180], [180, 230, 255]],
  aurora: [[20, 220, 160], [80, 40, 220], [40, 80, 180]],
  rainbow: null,
  sunset: [[255, 70, 20], [120, 10, 80], [255, 180, 60]],
  forest: [[0, 90, 30], [20, 160, 50], [0, 30, 10]],
  icefire: [[180, 220, 255], [255, 255, 255], [80, 140, 255]],
  candy: [[255, 0, 48], [255, 255, 255]],
  party: [[255, 0, 80], [0, 180, 255], [255, 220, 0]],
};

function clamp(value) {
  return Math.max(0, Math.min(255, Math.round(value)));
}

function mix(a, b, t) {
  return [
    clamp(a[0] + (b[0] - a[0]) * t),
    clamp(a[1] + (b[1] - a[1]) * t),
    clamp(a[2] + (b[2] - a[2]) * t),
  ];
}

function hue(value) {
  const h = ((value % 1) + 1) % 1;
  const x = 1 - Math.abs((h * 6) % 2 - 1);
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 1 / 6) [r, g, b] = [1, x, 0];
  else if (h < 2 / 6) [r, g, b] = [x, 1, 0];
  else if (h < 3 / 6) [r, g, b] = [0, 1, x];
  else if (h < 4 / 6) [r, g, b] = [0, x, 1];
  else if (h < 5 / 6) [r, g, b] = [x, 0, 1];
  else [r, g, b] = [1, 0, x];
  return [clamp(r * 255), clamp(g * 255), clamp(b * 255)];
}

function hash(n) {
  const x = Math.sin(n * 127.1) * 43758.5453;
  return x - Math.floor(x);
}

// Value noise that changes smoothly from one LED to the next and over time, so
// flicker looks like soft patches of light rather than stripes.
function smooth(x, seed) {
  const base = Math.floor(x);
  let f = x - base;
  f = f * f * (3 - 2 * f);
  return hash(base + seed * 57.3) * (1 - f) + hash(base + 1 + seed * 57.3) * f;
}

function paletteColor(look, phase, clock) {
  const name = String(look.palette || "").toLowerCase();
  if (name.includes("rainbow")) return hue(phase + clock * 0.05);
  const stops = PALETTES[name] || look.colors || [[255, 214, 170]];
  const scaled = phase * (stops.length - 1);
  const index = Math.floor(scaled);
  const next = Math.min(stops.length - 1, index + 1);
  return mix(stops[index], stops[next], scaled - index);
}

function shade(rgb, brightness, on) {
  if (!on) return [8, 8, 10];
  const level = Math.max(0, Math.min(255, brightness)) / 255;
  return rgb.map((channel) => clamp(channel * level));
}

/**
 * @param {object} look
 * @param {number} count
 * @param {number} now milliseconds
 */
export function renderEffect(look, count, now) {
  const width = Math.max(1, count);
  const colors = (look.colors && look.colors.length ? look.colors : [[255, 214, 170], [0, 0, 0], [0, 0, 0]]).map((color) => [
    color[0] || 0,
    color[1] || 0,
    color[2] || 0,
  ]);
  const primary = colors[0];
  const secondary = colors[1] || [0, 0, 0];
  const tertiary = colors[2] || secondary;
  const name = String(look.effect || "Solid").toLowerCase();
  const speed = Math.max(0, Math.min(255, look.sx ?? 128)) / 255;
  const clock = (now / 1000) * (0.35 + speed * 2.4);
  const on = look.on !== false && (look.bri ?? 255) > 0;
  const bri = look.bri ?? 255;
  const pixels = [];
  for (let index = 0; index < width; index += 1) {
    const phase = width === 1 ? 0 : index / (width - 1);
    let rgb = primary;
    if (name.includes("colorloop")) {
      // Colorloop cycles the whole strip through the hues together.
      rgb = hue(clock * 0.08);
    } else if (name.includes("aurora")) {
      // Curtains of green and violet that drift and swell.
      const band = smooth(phase * 9 + clock * 0.5, 5);
      const glow = 0.25 + 0.75 * smooth(phase * 15 - clock * 0.8, 6);
      rgb = mix(mix([10, 200, 120], [120, 40, 220], band), [0, 0, 0], 1 - glow);
    } else if (name.includes("colorwave") && !String(look.palette || "").toLowerCase().includes("rainbow")) {
      // Colorwaves runs its palette (Aurora, Ocean, ...) in slow swells.
      const at = (phase * 1.6 + clock * 0.05 + 0.15 * Math.sin(phase * 9 + clock)) % 1;
      const glow = 0.35 + 0.65 * smooth(phase * 12 - clock * 0.7, 7);
      rgb = mix(paletteColor(look, at, clock), [0, 0, 0], 1 - glow);
    } else if (name.includes("rainbow") || name.includes("colorwave")) {
      rgb = hue(phase * 0.85 + clock * 0.08);
    } else if (name.includes("ocean") || name.includes("pacifica") || name.includes("lake")) {
      const wave = 0.5 + 0.5 * Math.sin(phase * 8 + clock * 2);
      rgb = mix([0, 30, 80], [80, 200, 220], wave * phase);
    } else if (name.includes("rain")) {
      const drop = hash(Math.floor(clock * 3) * 13 + index) > 0.82;
      rgb = drop ? [180, 210, 255] : mix(secondary, primary, 0.15);
    } else if (name.includes("candle") || (name.includes("flicker") && !name.includes("fire"))) {
      // A candle flickers in broad, drifting patches and never drops far: dim to
      // a deeper orange, not toward brown.
      const flick = 0.45 + 0.55 * smooth(phase * 22 + clock * 1.6, 1) * (0.7 + 0.3 * smooth(clock * 4 + phase * 5, 2));
      rgb = mix([primary[0], primary[1] * 0.55, primary[2] * 0.3], primary, flick).map((c) => clamp(c * (0.3 + 0.7 * flick)));
    } else if (name.includes("ember") || name.includes("fire")) {
      // Heat drifts along the strip: deep red where it is cool, gold where it is hot.
      const heat = smooth(phase * 18 + clock * 0.9, 3) * 0.75 + 0.25 * smooth(phase * 41 - clock * 2.2, 4);
      rgb = heat < 0.5 ? mix([150, 18, 0], [255, 70, 0], heat * 2) : mix([255, 70, 0], [255, 190, 60], (heat - 0.5) * 2);
    } else if (name.includes("breathe") || name.includes("pulse") || name.includes("heartbeat")) {
      const beat = name.includes("heart")
        ? 0.12 + 0.88 * Math.pow(Math.max(0, Math.sin(clock * 3)), 6)
        : 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(clock * 2));
      rgb = mix([0, 0, 0], primary, beat);
    } else if (name.includes("twinkle") || name.includes("sparkle") || name.includes("firework")) {
      const onPixel = hash(Math.floor(clock * 2) * 17 + index * 3) > 0.78;
      rgb = onPixel ? primary : mix(secondary, [0, 0, 0], 0.6);
    } else if (name.includes("meteor") || name.includes("scan") || name.includes("scanner")) {
      const head = (clock * 0.35) % 1;
      const dist = Math.abs(phase - head);
      // A head with a fading tail behind it.
      const behind = (head - phase + 1) % 1;
      rgb = mix(secondary, primary, Math.max(0, 1 - behind * width / 14));
    } else if (
      name.includes("chase") ||
      name.includes("theater") ||
      name.includes("halloween") ||
      name.includes("christmas") ||
      name.includes("candy") ||
      name.includes("patriotic")
    ) {
      const band = Math.floor(index + clock * 6) % 6;
      rgb = band < 2 ? primary : band < 4 ? secondary : tertiary;
    } else if (name.includes("sunset") || name.includes("noise")) {
      rgb = paletteColor(look, (phase + clock * 0.05) % 1, clock);
    } else if (name !== "solid") {
      const wave = 0.5 + 0.5 * Math.sin(phase * 10 + clock * 3);
      rgb = mix(secondary, primary, wave);
    }
    pixels.push(shade(rgb, bri, on));
  }
  return pixels;
}

export function dominant(pixels) {
  if (!pixels || !pixels.length) return [232, 232, 234];
  let best = pixels[0];
  let score = 0;
  for (const pixel of pixels) {
    const value = pixel[0] + pixel[1] + pixel[2];
    if (value > score) {
      score = value;
      best = pixel;
    }
  }
  return best;
}
