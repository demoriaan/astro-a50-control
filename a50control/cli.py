"""Small command line front end, handy for scripts and debugging."""
from __future__ import annotations

import argparse
import sys

from . import protocol as p
from .device import A50
from .transport import DeviceBusy, DeviceGone, DeviceNotFound, DevicePermissionError, ForeignTraffic, Timeout, \
    Transport, find_devices

NOISE_GATE_NAMES = {m.name.lower(): m for m in p.NoiseGate}
MIC_EQ_NAMES = {m.name.lower(): m for m in p.MicEq}


def open_device(path: str | None) -> A50:
    paths = [path] if path else find_devices()
    if not paths:
        raise DeviceNotFound("no ASTRO A50 Gen 4 base station found")
    return A50(Transport(paths[0]))


def print_status(dev: A50) -> None:
    s = dev.read_all()
    print(f"Base station : FW {s.base.version}.{s.base_minor} (built {s.base.build_date})")
    headset = "on" if s.headset_on else "off"
    print(f"Headset      : {headset}{', docked' if s.docked else ''}"
          + (f", FW {s.headset_version}" if s.headset_version else ""))
    print(f"Battery      : {s.battery}%{' (charging)' if s.charging else ''}")
    print(f"Game/Voice   : {s.mix}/255   default {s.live.balance}/255   (255 = all game)")
    print(f"Unsaved      : {'yes' if s.dirty else 'no'}")
    for i, preset in enumerate(s.live.presets):
        mark = "*" if i == s.active_preset else " "
        bands = "  ".join(f"{b.gain:+3d}dB@{b.freq}Hz" + (f"/{b.bandwidth:.1f}" if b.bandwidth else "")
                          for b in preset.bands)
        print(f"EQ {i + 1}{mark}        : {preset.name:12s} {bands}")
    names = {sl: sl.name.lower() for sl in p.Slider}
    print("Sliders      : " + ", ".join(f"{names[k]}={v}" for k, v in sorted(s.live.sliders.items())))
    print(f"Noise gate   : {p.NoiseGate(s.live.noise_gate).name.lower()}")
    print(f"Mic EQ       : {p.MicEq(s.live.mic_eq).name.lower()}")
    print(f"Alert volume : {s.live.alert_volume}")
    print(f"LED          : {s.live.brightness}   auto-off: {p.AUTO_OFF_MINUTES[s.live.auto_off]} min")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="a50control-cli", description="Control an ASTRO A50 Gen 4 base station")
    ap.add_argument("--device", help="hidraw node (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="print all settings")
    sp = sub.add_parser("preset", help="activate EQ preset 1-3")
    sp.add_argument("index", type=int, choices=range(1, p.PRESET_COUNT + 1))
    sp = sub.add_parser("slider", help="set a level 0-100")
    sp.add_argument("name", choices=[s.name.lower() for s in p.Slider])
    sp.add_argument("value", type=int)
    sp = sub.add_parser("noise-gate", help="set the noise gate mode")
    sp.add_argument("mode", choices=list(NOISE_GATE_NAMES))
    sp = sub.add_parser("mic-eq", help="set the microphone EQ")
    sp.add_argument("mode", choices=list(MIC_EQ_NAMES))
    sub.add_parser("save", help="store the current settings on the headset")
    args = ap.parse_args(argv)

    try:
        dev = open_device(args.device)
    except DevicePermissionError as exc:
        print(f"{exc}. Install data/70-astro-a50.rules (see README).", file=sys.stderr)
        return 2
    except (DeviceNotFound, DeviceBusy) as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        if args.cmd in (None, "status"):
            print_status(dev)
        elif args.cmd == "preset":
            dev.set_active_preset(args.index - 1)
        elif args.cmd == "slider":
            dev.set_slider(p.Slider[args.name.upper()], args.value)
        elif args.cmd == "noise-gate":
            dev.set_noise_gate(NOISE_GATE_NAMES[args.mode])
        elif args.cmd == "mic-eq":
            dev.set_mic_eq(MIC_EQ_NAMES[args.mode])
        elif args.cmd == "save":
            dev.save()
    except (Timeout, DeviceGone, ForeignTraffic, p.DeviceError, p.ProtocolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
