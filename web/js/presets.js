// EQ preset library: ACC factory presets and ACC-compatible .ini import/export.
import * as p from "./protocol.js";

// [gains, centre frequencies, bandwidths of bands 2-4] from the AstroCommandCenter.msi library.
const FACTORY = {
  ASTRO: [[5, -2, -1, 7, 1], [95, 406, 783, 3901, 6339], [2.0, 2.0, 0.5]],
  STUDIO: [[0, 0, 0, 5, 7], [95, 419, 783, 3499, 6100], [1.0, 2.0, 1.0]],
  TOURNAMENT: [[6, -5, 0, 7, 7], [90, 406, 783, 4001, 7001], [2.0, 2.0, 2.0]],
  BALANCED: [[2, -3, 4, 3, 5], [95, 298, 1000, 2600, 6498], [2.0, 2.0, 2.0]],
  MEDIA: [[0, -3, 0, 2, 4], [95, 406, 783, 3901, 6339], [2.0, 2.0, 1.7]],
  "NATURAL BASS": [[0, 0, 0, 0, 0], [95, 271, 783, 2203, 6339], [2.0, 2.0, 2.0]],
  "MOD KIT": [[-4, -2, -3, 7, 7], [95, 419, 600, 3499, 6100], [1.0, 1.0, 1.6]],
};

export function factoryPresets() {
  return Object.entries(FACTORY).map(([name, [gains, freqs, widths]]) => {
    const bws = [0, ...widths, 0];
    return { name, bands: gains.map((gain, i) => ({ gain, freq: freqs[i], bandwidth: bws[i] })) };
  });
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Parse an ACC preset file ([General] Name=..., [Eq_Bands] Eq_* keys). */
export function parseIni(text, fallbackName = "Import") {
  const sections = new Map();
  let current = null;
  for (const raw of text.replace(/^﻿/, "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith(";") || line.startsWith("#")) continue;
    const head = line.match(/^\[(.+)\]$/);
    if (head) {
      if (!sections.has(head[1])) sections.set(head[1], new Map());
      current = sections.get(head[1]);
    } else if (current && line.includes("=")) {
      const at = line.indexOf("=");
      current.set(line.slice(0, at).trim(), line.slice(at + 1).trim());
    }
  }
  const eq = sections.get("Eq_Bands");
  if (!eq) throw new Error("keine [Eq_Bands]-Sektion gefunden");
  const num = (key, fallback) => {
    const v = Number(eq.get(key) ?? fallback);
    if (!Number.isFinite(v)) throw new Error(`ungültiger Wert für ${key}`);
    return v;
  };
  const bands = [];
  for (let i = 1; i <= p.BAND_COUNT; i++) {
    const peaking = p.PEAKING_BANDS.includes(i - 1);
    const width = peaking ? clamp(num(`Eq_Bandwidth_${i}`, 0) || 1, p.BW_MIN, p.BW_MAX) : 0;
    bands.push({
      gain: clamp(Math.round(num(`Eq_Band_${i}`, 0)), p.GAIN_MIN, p.GAIN_MAX),
      freq: clamp(Math.round(num(`Eq_Freq_Band_${i}`, 1000)), p.FREQ_MIN, p.FREQ_MAX),
      bandwidth: Math.round(width * 10) / 10,
    });
  }
  return { name: p.clipName(sections.get("General")?.get("Name") || fallbackName), bands };
}

export function toIni(preset) {
  const name = preset.name.replace(/[\r\n]+/g, " ");
  const lines = ["[General]", `Name=${name}`, "", "[Eq_Bands]"];
  preset.bands.forEach((band, i) => {
    const n = i + 1;
    lines.push(`Eq_Band_${n}=${band.gain}`, `Eq_Freq_Band_${n}=${band.freq}`);
    if (p.PEAKING_BANDS.includes(i)) lines.push(`Eq_Bandwidth_${n}=${band.bandwidth}`);
  });
  return lines.join("\n") + "\n";
}
