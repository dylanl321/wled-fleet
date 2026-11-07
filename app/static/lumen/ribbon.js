/** Live ribbons: linear-light downsample, blur, and 60 fps frame blend. */

import { dominant } from "./effects.js";

const GAMMA = 2.2;
let noise;

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

function noiseCanvas() {
  if (noise) return noise;
  noise = document.createElement("canvas");
  noise.width = 64;
  noise.height = 16;
  const ctx = noise.getContext("2d");
  const image = ctx.createImageData(64, 16);
  for (let i = 0; i < image.data.length; i += 4) {
    const value = Math.random() * 255;
    image.data[i] = value;
    image.data[i + 1] = value;
    image.data[i + 2] = value;
    image.data[i + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return noise;
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
      gradient.addColorStop(stop, `rgb(${color[0]},${color[1]},${color[2]})`);
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
  const falloff = ctx.createLinearGradient(0, 0, 0, height);
  falloff.addColorStop(0, "rgba(255,255,255,0.16)");
  falloff.addColorStop(0.42, "rgba(255,255,255,0)");
  falloff.addColorStop(1, "rgba(0,0,0,0.42)");
  ctx.fillStyle = falloff;
  ctx.fillRect(0, 0, width, height);
  ctx.globalAlpha = 0.03;
  const drift = (now / 40) % 64;
  ctx.drawImage(noiseCanvas(), -drift, 0, width + 64, height);
  ctx.globalAlpha = 1;
}

export function drawRibbon(canvas, pixels, options = {}) {
  if (!canvas || !canvas.isConnected) return;
  const { width, height, dpr } = ensureSize(canvas);
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const fitted = downsample(pixels, Math.max(1, Math.round(width)));
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
