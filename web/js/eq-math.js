// Approximate magnitude response of the 5-band EQ for drawing (port of a50control/eq_math.py).
// Bands 1 and 5 are modelled as RBJ shelves, 2-4 as RBJ peaking filters with the
// bandwidth value read as octaves; the firmware's exact filter design is unknown.

const SAMPLE_RATE = 48000;

function coeffs(kind, band) {
  const a = 10 ** (band.gain / 40);
  const w0 = (2 * Math.PI * Math.min(band.freq, SAMPLE_RATE * 0.45)) / SAMPLE_RATE;
  const cw = Math.cos(w0);
  const sw = Math.sin(w0);
  if (kind === "peak") {
    const bw = Math.max(band.bandwidth, 0.05);
    const alpha = sw * Math.sinh((Math.LN2 / 2) * bw * (w0 / sw));
    return [1 + alpha * a, -2 * cw, 1 - alpha * a, 1 + alpha / a, -2 * cw, 1 - alpha / a];
  }
  const sa = 2 * Math.sqrt(a) * ((sw / 2) * Math.SQRT2);
  if (kind === "low") {
    return [a * ((a + 1) - (a - 1) * cw + sa), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - sa),
      (a + 1) + (a - 1) * cw + sa, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - sa];
  }
  return [a * ((a + 1) + (a - 1) * cw + sa), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - sa),
    (a + 1) - (a - 1) * cw + sa, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - sa];
}

export const bandKind = (i, count = 5) => (i === 0 ? "low" : i === count - 1 ? "high" : "peak");

/** Summed response in dB of all bands at the given frequencies. */
export function responseDb(bands, freqs) {
  const filters = bands.map((b, i) => (b.gain ? coeffs(bandKind(i, bands.length), b) : null)).filter(Boolean);
  return freqs.map((f) => {
    const w = (2 * Math.PI * f) / SAMPLE_RATE;
    // z^-1 = e^{-jw}
    const c1 = Math.cos(w), s1 = -Math.sin(w), c2 = Math.cos(2 * w), s2 = -Math.sin(2 * w);
    let total = 0;
    for (const [b0, b1, b2, a0, a1, a2] of filters) {
      const nr = b0 + b1 * c1 + b2 * c2, ni = b1 * s1 + b2 * s2;
      const dr = a0 + a1 * c1 + a2 * c2, di = a1 * s1 + a2 * s2;
      total += 10 * Math.log10(Math.max((nr * nr + ni * ni) / (dr * dr + di * di), 1e-18));
    }
    return total;
  });
}

export function logFreqs(lo = 20, hi = 20000, n = 240) {
  const step = Math.log(hi / lo) / (n - 1);
  return Array.from({ length: n }, (_, i) => lo * Math.exp(i * step));
}
