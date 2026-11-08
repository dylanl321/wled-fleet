/** Live ribbons: linear-light downsample, blur, and 60 fps frame blend. */

import { dominant } from "./effects.js";

const GAMMA = 2.2;

function linear(channel) {
  return (Math.max(0, Math.min(255, channel)) / 255) ** GAMMA;
}

function encoded(value) {
  return Math.max(0, Math.min(255, Math.round(value ** (1 / GAMMA) * 255)));
}

export function downsample(colors, width) {
  if (!colors || !colors.length || width <= 0) return [];
  if (colors.length <= width) return colors.map((color) => color.slice(0, 3));
  const step = colors.length / width;
  const sampled = [];
  for (let index = 0; index < width; index += 1) {
    const start = Math.floor(index * step);
    const stop = Math.max(start + 1, Math.floor((index + 1) * step));
    const total = [0, 0, 0];
    const count = stop - start;
    for (let cursor = start; cursor < stop; cursor += 1) {
      const color = colors[cursor];
      total[0] += linear(color[0]);
      total[1] += linear(color[1]);
      total[2] += linear(color[2]);
    }
    sampled.push([encoded(total[0] / count), encoded(total[1] / count), encoded(total[2] / count)]);
  }
  return sampled;
}

function meanDelta(a, b) {
  if (!a || !b || !a.length || !b.length) return 0;
  const width = Math.min(a.length, b.length);
  let total = 0;
  for (let index = 0; index < width; index += 1) {
    total += Math.abs(a[index][0] - b[index][0]) + Math.abs(a[index][1] - b[index][1]) + Math.abs(a[index][2] - b[index][2]);
  }
  return total / (width * 3);
}

export function blendFrames(previous, next, amount) {
  if (!previous || !next) return next || previous || [];
  if (meanDelta(previous, next) > 70) return amount < 0.5 ? previous : next;
  const width = Math.min(previous.length, next.length);
  const mixed = [];
  for (let index = 0; index < width; index += 1) {
    mixed.push([
      encoded(linear(previous[index][0]) * (1 - amount) + linear(next[index][0]) * amount),
      encoded(linear(previous[index][1]) * (1 - amount) + linear(next[index][1]) * amount),
      encoded(linear(previous[index][2]) * (1 - amount) + linear(next[index][2]) * amount),
    ]);
  }
  return mixed;
}

// LED values are PWM duty, which the eye sees as much brighter than the same
// number on a screen: drawn as-is, a half-bright amber strip looks brown. Keep
// each LED's hue and saturation (its color at full output), and map only its
// level through a perceptual curve, so a dimmed strip stays the same color, less
// bright, the way it looks in the room.
function toScreen(color) {
  const level = Math.max(color[0], color[1], color[2]);
  if (level <= 0) return [0, 0, 0];
  const shown = 255 * (level / 255) ** 0.5;
  const scale = shown / level;
  return [Math.round(color[0] * scale), Math.round(color[1] * scale), Math.round(color[2] * scale)];
}

// A slow, smooth luminance drift (about 3 %) so a solid strip still feels lit,
// without grain: no random texture is drawn over the ribbon.
function shimmer(index, count, now) {
  const x = count > 1 ? index / (count - 1) : 0;
  const t = now / 1000;
  return 1 + 0.03 * Math.sin(t * 0.7 + x * 5.1) * Math.sin(t * 0.43 + x * 2.3 + 1.7);
}

function lit(color, factor) {
  return `rgb(${Math.min(255, Math.round(color[0] * factor))},${Math.min(255, Math.round(color[1] * factor))},${Math.min(255, Math.round(color[2] * factor))})`;
}

function ensureSize(canvas) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.max(1, canvas.clientWidth);
  const height = Math.max(1, canvas.clientHeight);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  return { width, height, dpr };
}

function paintPixels(ctx, pixels, width, height, now, flow) {
  const short = pixels.length > 0 && pixels.length < 20;
  ctx.clearRect(0, 0, width, height);
  if (!pixels.length) {
    ctx.fillStyle = "#141416";
    ctx.fillRect(0, 0, width, height);
    return;
  }
  if (short) {
    ctx.fillStyle = "#141416";
    ctx.fillRect(0, 0, width, height);
    const radius = Math.max(6, width / pixels.length / 1.4);
    pixels.forEach((color, index) => {
      const x = ((index + 0.5) / pixels.length) * width;
      const gradient = ctx.createRadialGradient(x, height / 2, 0, x, height / 2, radius);
      gradient.addColorStop(0, `rgba(${color[0]},${color[1]},${color[2]},0.95)`);
      gradient.addColorStop(1, `rgba(${color[0]},${color[1]},${color[2]},0)`);
      ctx.fillStyle = gradient;
      ctx.fillRect(x - radius, 0, radius * 2, height);
    });
  } else {
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    pixels.forEach((color, index) => {
      const stop = pixels.length === 1 ? 0 : index / (pixels.length - 1);
      gradient.addColorStop(stop, lit(color, shimmer(index, pixels.length, now)));
    });
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);
  }
  if (flow && flow.amount < 1 && flow.from) {
    ctx.save();
    const edge = width * flow.amount;
    ctx.beginPath();
    ctx.rect(edge, 0, width - edge, height);
    ctx.clip();
    const old = ctx.createLinearGradient(0, 0, width, 0);
    flow.from.forEach((color, index) => {
      const stop = flow.from.length === 1 ? 0 : index / (flow.from.length - 1);
      old.addColorStop(Math.min(1, stop), `rgb(${color[0]},${color[1]},${color[2]})`);
    });
    ctx.fillStyle = old;
    ctx.fillRect(0, 0, width, height);
    const fade = ctx.createLinearGradient(edge - 18, 0, edge + 8, 0);
    fade.addColorStop(0, "rgba(0,0,0,0)");
    fade.addColorStop(1, "rgba(255,255,255,0.18)");
    ctx.fillStyle = fade;
    ctx.fillRect(edge - 18, 0, 26, height);
    ctx.restore();
  }
  // A soft core of the strip's own color, faded smoothly to nothing above and
  // below, so the ribbon reads as emitted light rather than painted plastic.
  if (!short) {
    const source = ctx.canvas;
    let glow = source._core;
    if (!glow) {
      glow = document.createElement("canvas");
      source._core = glow;
    }
    if (glow.width !== source.width || glow.height !== source.height) {
      glow.width = source.width;
      glow.height = source.height;
    }
    const gctx = glow.getContext("2d");
    gctx.setTransform(1, 0, 0, 1, 0, 0);
    gctx.globalCompositeOperation = "copy";
    gctx.drawImage(source, 0, 0);
    gctx.globalCompositeOperation = "destination-in";
    const mask = gctx.createLinearGradient(0, 0, 0, glow.height);
    mask.addColorStop(0, "rgba(0,0,0,0)");
    mask.addColorStop(0.5, "rgba(0,0,0,1)");
    mask.addColorStop(1, "rgba(0,0,0,0)");
    gctx.fillStyle = mask;
    gctx.fillRect(0, 0, glow.width, glow.height);
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "screen";
    ctx.globalAlpha = 0.3;
    ctx.drawImage(glow, 0, 0);
    ctx.restore();
  }
  // Light on a surface: brightest along the middle, falling off gently at both
  // edges. No white highlight, which read as a lacquered plank.
  const falloff = ctx.createLinearGradient(0, 0, 0, height);
  falloff.addColorStop(0, "rgba(0,0,0,0.22)");
  falloff.addColorStop(0.35, "rgba(0,0,0,0)");
  falloff.addColorStop(0.65, "rgba(0,0,0,0)");
  falloff.addColorStop(1, "rgba(0,0,0,0.26)");
  ctx.fillStyle = falloff;
  ctx.fillRect(0, 0, width, height);
}

export function drawRibbon(canvas, pixels, options = {}) {
  if (!canvas || !canvas.isConnected) return;
  const { width, height, dpr } = ensureSize(canvas);
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const fitted = downsample(pixels, Math.max(1, Math.round(width))).map(toScreen);
  let off = canvas._off;
  if (!off) {
    off = document.createElement("canvas");
    canvas._off = off;
  }
  if (off.width !== canvas.width || off.height !== canvas.height) {
    off.width = canvas.width;
    off.height = canvas.height;
  }
  const octx = off.getContext("2d", { alpha: false });
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  paintPixels(octx, fitted, width, height, options.now || performance.now(), options.flow);
  ctx.filter = options.desaturate ? "blur(1.5px) saturate(0.4)" : "blur(1.5px)";
  ctx.drawImage(off, 0, 0, width, height);
  ctx.filter = "none";
  canvas.dataset.glow = dominant(fitted).join(",");
}

export function frameAt(frames, now) {
  if (!frames) return [];
  const lag = now - 100;
  if (!frames.previous) return frames.leds || [];
  const span = frames.at - frames.previousAt;
  if (span <= 0) return frames.leds;
  const amount = Math.max(0, Math.min(1, (lag - frames.previousAt) / span));
  return blendFrames(frames.previous, frames.leds, amount);
}
