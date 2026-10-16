"""Approximate magnitude response of the 5-band headset EQ for drawing.

The firmware's filter design is not documented. Bands 1 and 5 have no
bandwidth parameter, so they are modelled as RBJ low/high shelves; bands 2-4 as
RBJ peaking filters with the bandwidth value interpreted in octaves.
"""
from __future__ import annotations

import cmath
import math

from .protocol import Band

SAMPLE_RATE = 48000.0


def _coeffs(kind: str, band: Band) -> tuple[float, ...]:
    a = 10 ** (band.gain / 40.0)
    w0 = 2 * math.pi * min(band.freq, SAMPLE_RATE * 0.45) / SAMPLE_RATE
    cw, sw = math.cos(w0), math.sin(w0)
    if kind == "peak":
        bw = max(band.bandwidth, 0.05)
        alpha = sw * math.sinh(math.log(2) / 2 * bw * w0 / sw)
        return (1 + alpha * a, -2 * cw, 1 - alpha * a, 1 + alpha / a, -2 * cw, 1 - alpha / a)
    alpha = sw / 2 * math.sqrt(2)
    sa = 2 * math.sqrt(a) * alpha
    if kind == "low":
        return (a * ((a + 1) - (a - 1) * cw + sa), 2 * a * ((a - 1) - (a + 1) * cw),
                a * ((a + 1) - (a - 1) * cw - sa), (a + 1) + (a - 1) * cw + sa,
                -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - sa)
    return (a * ((a + 1) + (a - 1) * cw + sa), -2 * a * ((a - 1) + (a + 1) * cw),
            a * ((a + 1) + (a - 1) * cw - sa), (a + 1) - (a - 1) * cw + sa,
            2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - sa)


def band_kind(index: int, count: int = 5) -> str:
    if index == 0:
        return "low"
    if index == count - 1:
        return "high"
    return "peak"


def response_db(bands: list[Band], freqs: list[float]) -> list[float]:
    """Sum of all band responses in dB at the given frequencies."""
    filters = [_coeffs(band_kind(i, len(bands)), b) for i, b in enumerate(bands) if b.gain]
    out = []
    for f in freqs:
        z = cmath.exp(-1j * 2 * math.pi * f / SAMPLE_RATE)
        total = 0.0
        for b0, b1, b2, a0, a1, a2 in filters:
            h = (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)
            total += 20 * math.log10(max(abs(h), 1e-9))
        out.append(total)
    return out


def log_freqs(lo: float = 20.0, hi: float = 20000.0, n: int = 240) -> list[float]:
    step = math.log(hi / lo) / (n - 1)
    return [lo * math.exp(i * step) for i in range(n)]
