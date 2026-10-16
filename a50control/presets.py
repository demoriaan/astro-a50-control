"""EQ preset library: ACC factory presets plus ACC-compatible .ini import/export."""
from __future__ import annotations

import configparser
from pathlib import Path

from . import protocol as p
from .protocol import Band, Preset

# (gains, centre frequencies, bandwidths of bands 2-4) as shipped in the
# AstroCommandCenter.msi preset library (Eq_Band_n / Eq_Freq_Band_n / Eq_Bandwidth_n).
_FACTORY = {
    "ASTRO": ((5, -2, -1, 7, 1), (95, 406, 783, 3901, 6339), (2.0, 2.0, 0.5)),
    "STUDIO": ((0, 0, 0, 5, 7), (95, 419, 783, 3499, 6100), (1.0, 2.0, 1.0)),
    "TOURNAMENT": ((6, -5, 0, 7, 7), (90, 406, 783, 4001, 7001), (2.0, 2.0, 2.0)),
    "BALANCED": ((2, -3, 4, 3, 5), (95, 298, 1000, 2600, 6498), (2.0, 2.0, 2.0)),
    "MEDIA": ((0, -3, 0, 2, 4), (95, 406, 783, 3901, 6339), (2.0, 2.0, 1.7)),
    "NATURAL BASS": ((0, 0, 0, 0, 0), (95, 271, 783, 2203, 6339), (2.0, 2.0, 2.0)),
    "MOD KIT": ((-4, -2, -3, 7, 7), (95, 419, 600, 3499, 6100), (1.0, 1.0, 1.6)),
}


def _make(name: str, gains, freqs, widths) -> Preset:
    bws = (0.0, *widths, 0.0)
    return Preset(name, [Band(g, f, w) for g, f, w in zip(gains, freqs, bws)])


def factory_presets() -> list[Preset]:
    return [_make(name, *values) for name, values in _FACTORY.items()]


def load_ini(path: str | Path) -> Preset:
    """Read an ACC preset file ([General] Name, [Eq_Bands] Eq_* keys)."""
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    if not cfg.read(path, encoding="utf-8-sig"):
        raise ValueError(f"cannot read {path}")
    eq = cfg["Eq_Bands"]
    name = cfg.get("General", "Name", fallback=Path(path).stem)
    bands = []
    for i in range(1, p.BAND_COUNT + 1):
        gain = round(float(eq.get(f"Eq_Band_{i}", "0")))
        freq = round(float(eq.get(f"Eq_Freq_Band_{i}", "1000")))
        width = float(eq.get(f"Eq_Bandwidth_{i}", "0")) if i - 1 in p.PEAKING_BANDS else 0.0
        if i - 1 in p.PEAKING_BANDS:
            width = min(p.BW_MAX, max(p.BW_MIN, width or 1.0))
        bands.append(Band(max(p.GAIN_MIN, min(p.GAIN_MAX, gain)),
                          max(p.FREQ_MIN, min(p.FREQ_MAX, freq)), round(width, 1)))
    return Preset(name[:p.NAME_MAX], bands)


def save_ini(preset: Preset, path: str | Path) -> None:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg["General"] = {"Name": preset.name}
    eq = {}
    for i, band in enumerate(preset.bands, start=1):
        eq[f"Eq_Band_{i}"] = str(band.gain)
        eq[f"Eq_Freq_Band_{i}"] = str(band.freq)
        if i - 1 in p.PEAKING_BANDS:
            eq[f"Eq_Bandwidth_{i}"] = f"{band.bandwidth:g}"
    cfg["Eq_Bands"] = eq
    with open(path, "w", encoding="utf-8") as fh:
        cfg.write(fh, space_around_delimiters=False)
