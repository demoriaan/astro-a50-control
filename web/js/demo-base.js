// Simulated base station for the demo mode and the tests; answers like the real firmware.
import { Cmd, DeviceError } from "./protocol.js";

const u16 = (v) => [v & 0xff, v >> 8];
const u32 = (v) => [v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff];

export class FakeBase {
  constructor() {
    this.live = {
      gains: { 1: [6, -5, 0, 7, 7], 2: [-5, 7, -5, 6, 1], 3: [-5, -5, -2, 7, 7] },
      bands: Object.fromEntries([1, 2, 3].map((p) => [p, [[0, 90], [8192, 406], [8192, 783], [8192, 4001], [0, 7001]]])),
      names: { 1: "ASTRO+", 2: "PRO", 3: "STUDIO+" },
      sliders: [100, 81, 65, 0, 99, 0],
      noiseGate: 1, micEq: 2, balance: 159, alert: 100, bright: 70, autoOff: 15,
    };
    this.saved = structuredClone(this.live);
    this.active = 3;
    this.log = [];
    this.closed = false;
  }

  async close() { this.closed = true; }

  async transact(cmd, payload = []) {
    const pl = Array.from(payload);
    this.log.push([cmd, pl]);
    const lv = this.live;
    const pick = () => (pl[0] === 1 ? this.saved : lv);
    const bytes = (arr) => Uint8Array.from(arr);
    switch (cmd) {
      case Cmd.TX_INFO:
        return bytes([0, 0, 0, 0, ...u16(0x9886), ...u16(0x2c), ...u16(2022), 4, 20, 9, 4, 39, 0, 0, 0, 0, 0, 0, ...u32(40372), ...new Array(13).fill(0)]);
      case Cmd.BASE_FW_MINOR: case Cmd.HEADSET_FW_MINOR: return bytes([0x2b]);
      case Cmd.HEADSET_FW_MAJOR: return bytes(u32(39964));
      case Cmd.HEADSET_STATUS: return bytes([0x02]);
      case Cmd.GET_BATTERY: return bytes([0x4e]);
      case Cmd.GET_MIX: return bytes([0xff]);
      case Cmd.GET_ACTIVE_EQ: return bytes([this.active]);
      case Cmd.SET_ACTIVE_EQ: this.active = pl[0]; return bytes([cmd, pl[0]]);
      case Cmd.GET_EQ_GAINS:
        return bytes([cmd, pl[0], ...lv.gains[pl[0]].map((g) => g + 12), ...this.saved.gains[pl[0]].map((g) => g + 12)]);
      case Cmd.SET_EQ_GAINS: lv.gains[pl[0]] = pl.slice(1, 6).map((b) => b - 12); return bytes([cmd, pl[0]]);
      case Cmd.GET_EQ_BAND: {
        const [bw, f] = lv.bands[pl[0]][pl[1] - 1];
        const [sbw, sf] = this.saved.bands[pl[0]][pl[1] - 1];
        return bytes([cmd, pl[0], pl[1], ...u16(bw), ...u16(sbw), ...u16(f), ...u16(sf)]);
      }
      case Cmd.SET_EQ_BAND:
        lv.bands[pl[0]][pl[1] - 1] = [pl[2] | (pl[3] << 8), pl[4] | (pl[5] << 8)];
        return bytes([cmd, pl[0], pl[1]]);
      case Cmd.GET_EQ_NAME: {
        const name = new TextEncoder().encode((pl[1] ? this.saved : lv).names[pl[0]]);
        return bytes([cmd, pl[0], ...name, ...new Array(50 - name.length).fill(0)]);
      }
      case Cmd.SET_EQ_NAME: lv.names[pl[0]] = new TextDecoder().decode(Uint8Array.from(pl.slice(2, 2 + pl[1] - 1))); return bytes([cmd, pl[0]]);
      case Cmd.GET_SLIDER: return bytes([cmd, pl[0], lv.sliders[pl[0]], this.saved.sliders[pl[0]]]);
      case Cmd.SET_SLIDER: lv.sliders[pl[0]] = pl[1]; return bytes([cmd, pl[0]]);
      case Cmd.GET_NOISE_GATE: return bytes([cmd, lv.noiseGate, this.saved.noiseGate]);
      case Cmd.SET_NOISE_GATE: lv.noiseGate = pl[0]; return bytes([pl[0]]);
      case Cmd.GET_MIC_EQ: return bytes([pick().micEq]);
      case Cmd.GET_BALANCE: return bytes([pick().balance]);
      case Cmd.GET_ALERT_VOLUME: return bytes([pick().alert]);
      case Cmd.GET_AUTO_OFF: return bytes([pick().autoOff]);
      case Cmd.GET_BRIGHTNESS: return bytes(new Array(9).fill(pick().bright));
      case Cmd.SET_MIC_EQ: lv.micEq = pl[0]; return bytes([cmd]);
      case Cmd.SET_BALANCE: lv.balance = pl[0]; return bytes([cmd]);
      case Cmd.SET_ALERT_VOLUME: lv.alert = pl[0]; return bytes([cmd]);
      case Cmd.SET_AUTO_OFF: lv.autoOff = pl[0]; return bytes([cmd]);
      case Cmd.SET_BRIGHTNESS: lv.bright = pl[0]; return bytes([]);
      case Cmd.SAVE: this.saved = structuredClone(lv); return bytes([cmd, 0]);
      default: throw new DeviceError(cmd, 1, "HID_ERROR_UNHANDLED_PACKET");
    }
  }
}
