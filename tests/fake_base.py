"""In-memory simulation of the base station, modelled on the verified protocol."""
import copy
import struct

from a50control import protocol as p
from a50control.protocol import Cmd


class FakeBase:
    """Drop-in for Transport: answers transact() like the real firmware (Mimas)."""

    def __init__(self):
        self.live = {
            "gains": {1: [6, -5, 0, 7, 7], 2: [-5, 7, -5, 6, 1], 3: [-5, -5, -2, 7, 7]},
            "bands": {pr: [[0, 90], [8192, 406], [8192, 783], [8192, 4001], [0, 7001]] for pr in (1, 2, 3)},
            "names": {1: "ASTRO+", 2: "PRO", 3: "STUDIO+"},
            "sliders": [100, 81, 65, 0, 99, 0],
            "noise_gate": 1, "mic_eq": 2, "balance": 159, "alert": 100, "bright": 70, "auto_off": 15,
        }
        self.saved = copy.deepcopy(self.live)
        self.active = 3
        self.log = []
        self.closed = False

    def close(self):
        self.closed = True

    def _copy(self, payload):
        return self.saved if payload[:1] == b"\x01" else self.live

    def transact(self, cmd, payload=b"", timeout=1.0, retries=1):
        self.log.append((cmd, bytes(payload)))
        lv, pl = self.live, bytes(payload)
        if cmd == Cmd.TX_INFO:
            return bytes(4) + struct.pack("<HH", 0x9886, 0x2C) + struct.pack("<HBBBBB", 2022, 4, 20, 9, 4, 39) \
                + bytes(6) + struct.pack("<I", 40372) + bytes(13)
        if cmd in (Cmd.BASE_FW_MINOR, Cmd.HEADSET_FW_MINOR):
            return b"\x2b"
        if cmd == Cmd.HEADSET_FW_MAJOR:
            return struct.pack("<I", 39964)
        if cmd == Cmd.HEADSET_STATUS:
            return b"\x02"
        if cmd == Cmd.GET_BATTERY:
            return b"\x4e"
        if cmd == Cmd.GET_MIX:
            return b"\xff"
        if cmd == Cmd.GET_ACTIVE_EQ:
            return bytes([self.active])
        if cmd == Cmd.SET_ACTIVE_EQ:
            self.active = pl[0]
            return bytes([cmd, pl[0]])
        if cmd == Cmd.GET_EQ_GAINS:
            raw = [g + 12 for g in lv["gains"][pl[0]]] + [g + 12 for g in self.saved["gains"][pl[0]]]
            return bytes([cmd, pl[0]] + raw)
        if cmd == Cmd.SET_EQ_GAINS:
            lv["gains"][pl[0]] = [b - 12 for b in pl[1:6]]
            return bytes([cmd, pl[0]])
        if cmd == Cmd.GET_EQ_BAND:
            (bw, f), (sbw, sf) = lv["bands"][pl[0]][pl[1] - 1], self.saved["bands"][pl[0]][pl[1] - 1]
            return bytes([cmd, pl[0], pl[1]]) + struct.pack("<HHHH", bw, sbw, f, sf)
        if cmd == Cmd.SET_EQ_BAND:
            lv["bands"][pl[0]][pl[1] - 1] = list(struct.unpack_from("<HH", pl, 2))
            return bytes([cmd, pl[0], pl[1]])
        if cmd == Cmd.GET_EQ_NAME:
            name = (self.saved if pl[1] else lv)["names"][pl[0]].encode()
            return bytes([cmd, pl[0]]) + name + bytes(50 - len(name))
        if cmd == Cmd.SET_EQ_NAME:
            lv["names"][pl[0]] = pl[2:2 + pl[1] - 1].decode()
            return bytes([cmd, pl[0]])
        if cmd == Cmd.GET_SLIDER:
            return bytes([cmd, pl[0], lv["sliders"][pl[0]], self.saved["sliders"][pl[0]]])
        if cmd == Cmd.SET_SLIDER:
            lv["sliders"][pl[0]] = pl[1]
            return bytes([cmd, pl[0]])
        if cmd == Cmd.GET_NOISE_GATE:
            return bytes([cmd, lv["noise_gate"], self.saved["noise_gate"]])
        if cmd == Cmd.SET_NOISE_GATE:
            lv["noise_gate"] = pl[0]
            return bytes([pl[0]])
        simple = {Cmd.GET_MIC_EQ: "mic_eq", Cmd.GET_BALANCE: "balance", Cmd.GET_ALERT_VOLUME: "alert",
                  Cmd.GET_AUTO_OFF: "auto_off"}
        if cmd in simple:
            return bytes([self._copy(pl)[simple[cmd]]])
        if cmd == Cmd.GET_BRIGHTNESS:
            return bytes([self._copy(pl)["bright"]] * 9)
        setters = {Cmd.SET_MIC_EQ: "mic_eq", Cmd.SET_BALANCE: "balance", Cmd.SET_ALERT_VOLUME: "alert",
                   Cmd.SET_AUTO_OFF: "auto_off"}
        if cmd in setters:
            lv[setters[cmd]] = pl[0]
            return bytes([cmd])
        if cmd == Cmd.SET_BRIGHTNESS:
            lv["bright"] = pl[0]
            return b""
        if cmd == Cmd.SAVE:
            self.saved = copy.deepcopy(lv)
            return bytes([cmd, 0])
        raise p.DeviceError(cmd, 1, "HID_ERROR_UNHANDLED_PACKET")
