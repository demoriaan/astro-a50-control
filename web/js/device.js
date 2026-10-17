// High-level model of the base station; port of a50control/device.py.
import * as p from "./protocol.js";

const { Cmd } = p;
const LIVE = [0];
const SAVED = [1];
const SAVE_BUSY = 2;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function emptySettings() {
  return {
    presets: Array.from({ length: p.PRESET_COUNT }, () => ({
      name: "",
      bands: Array.from({ length: p.BAND_COUNT }, () => ({ gain: 0, freq: 1000, bandwidth: 0 })),
    })),
    sliders: {},
    noiseGate: p.NoiseGate.HOME,
    micEq: p.MicEq.BRIGHT,
    balance: 128,
    alertVolume: 100,
    brightness: p.BRIGHTNESS_MAX,
    autoOff: 0,
  };
}

export const clone = (obj) => structuredClone(obj);
export const sameSettings = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export class A50 {
  constructor(transport) {
    this.t = transport;
    this.state = {
      base: null, baseMinor: null, headsetVersion: null, headsetOn: false, docked: false,
      battery: 0, charging: false, mix: 128, activePreset: 0,
      live: emptySettings(), saved: emptySettings(),
    };
  }

  get dirty() {
    return !sameSettings(this.state.live, this.state.saved);
  }

  close() {
    return this.t.close();
  }

  async byte(cmd, payload = []) {
    const data = await this.t.transact(cmd, payload);
    if (!data.length) throw new p.ProtocolError(`empty response to 0x${p.hex(cmd)}`);
    return data[0];
  }

  // -- reading ------------------------------------------------------------

  async readAll() {
    const s = this.state;
    s.base = p.parseTxInfo(await this.t.transact(Cmd.TX_INFO));
    s.baseMinor = await this.byte(Cmd.BASE_FW_MINOR);
    await this.readStatus();
    for (let i = 0; i < p.PRESET_COUNT; i++) await this.readPreset(i);
    for (const sid of Object.values(p.Slider)) {
      const data = await this.t.transact(Cmd.GET_SLIDER, [sid]);
      [s.live.sliders[sid], s.saved.sliders[sid]] = p.parsePair(data, Cmd.GET_SLIDER, sid);
    }
    [s.live.noiseGate, s.saved.noiseGate] = p.parseNoiseGate(await this.t.transact(Cmd.GET_NOISE_GATE));
    for (const [sel, target] of [[LIVE, s.live], [SAVED, s.saved]]) {
      target.micEq = Math.min(await this.byte(Cmd.GET_MIC_EQ, sel), p.MicEq.WARM);
      target.balance = await this.byte(Cmd.GET_BALANCE, sel);
      target.alertVolume = Math.min(await this.byte(Cmd.GET_ALERT_VOLUME, sel), p.ALERT_MAX);
      target.brightness = p.parseBrightness(await this.t.transact(Cmd.GET_BRIGHTNESS, sel));
      target.autoOff = p.parseAutoOff(await this.t.transact(Cmd.GET_AUTO_OFF, sel));
    }
    return s;
  }

  /** Cheap poll: headset power/dock, battery, game/voice mix and active EQ preset. */
  async readStatus() {
    const s = this.state;
    Object.assign(s, p.parseHeadsetStatus(await this.t.transact(Cmd.HEADSET_STATUS)));
    Object.assign(s, p.parseBattery(await this.t.transact(Cmd.GET_BATTERY)));
    s.mix = await this.byte(Cmd.GET_MIX);
    s.activePreset = Math.max(1, Math.min(p.PRESET_COUNT, await this.byte(Cmd.GET_ACTIVE_EQ))) - 1;
    if (!s.headsetOn) s.headsetVersion = null;
    else if (s.headsetVersion === null) await this.readHeadsetVersion();
    return s;
  }

  async readHeadsetVersion() {
    const s = this.state;
    try {
      const major = p.parseU32(await this.t.transact(Cmd.HEADSET_FW_MAJOR, [], { timeout: 2500, retries: 0 }));
      const minor = await this.t.transact(Cmd.HEADSET_FW_MINOR, [], { timeout: 2500, retries: 0 });
      if (!minor.length) throw new p.ProtocolError("empty headset firmware answer");
      s.headsetVersion = `${major}.${minor[0]}`;
    } catch (err) {
      if (err.name === "DeviceGoneError") throw err;
      s.headsetVersion = ""; // unknown; do not retry until the headset reconnects
    }
  }

  async readPreset(index) {
    const live = this.state.live.presets[index];
    const saved = this.state.saved.presets[index];
    const [gains, savedGains] = p.parseEqGains(await this.t.transact(Cmd.GET_EQ_GAINS, [index + 1]), index);
    live.name = p.parseEqName(await this.t.transact(Cmd.GET_EQ_NAME, [index + 1, 0]), index);
    saved.name = p.parseEqName(await this.t.transact(Cmd.GET_EQ_NAME, [index + 1, 1]), index);
    for (let b = 0; b < p.BAND_COUNT; b++) {
      const [cur, old] = p.parseEqBand(await this.t.transact(Cmd.GET_EQ_BAND, [index + 1, b + 1]), index, b);
      live.bands[b] = { gain: gains[b], ...cur };
      saved.bands[b] = { gain: savedGains[b], ...old };
    }
  }

  // -- writing ------------------------------------------------------------

  async set(cmd, payload, ...echo) {
    const data = await this.t.transact(cmd, payload, { retries: 2 });
    p.expectEcho(data, cmd, ...echo);
  }

  async setActivePreset(index) {
    await this.set(Cmd.SET_ACTIVE_EQ, [index + 1], index + 1);
    this.state.activePreset = index;
  }

  async setGains(index, gains) {
    await this.set(Cmd.SET_EQ_GAINS, p.payloadEqGains(index, gains), index + 1);
    this.state.live.presets[index].bands.forEach((band, i) => {
      band.gain = Math.max(p.GAIN_MIN, Math.min(p.GAIN_MAX, Math.trunc(gains[i])));
    });
  }

  async setBand(index, band, freq, bandwidth) {
    const bw = p.PEAKING_BANDS.includes(band)
      ? Math.round(Math.max(p.BW_MIN, Math.min(p.BW_MAX, bandwidth)) * 10) / 10 : 0;
    await this.set(Cmd.SET_EQ_BAND, p.payloadEqBand(index, band, freq, bw), index + 1, band + 1);
    const target = this.state.live.presets[index].bands[band];
    target.freq = Math.max(p.FREQ_MIN, Math.min(p.FREQ_MAX, Math.trunc(freq)));
    target.bandwidth = bw;
  }

  async setName(index, name) {
    const clipped = p.clipName(name);
    await this.set(Cmd.SET_EQ_NAME, p.payloadEqName(index, clipped), index + 1);
    this.state.live.presets[index].name = clipped;
  }

  async applyPreset(index, preset) {
    await this.setGains(index, preset.bands.map((b) => b.gain));
    for (let b = 0; b < p.BAND_COUNT; b++) await this.setBand(index, b, preset.bands[b].freq, preset.bands[b].bandwidth);
    if (preset.name) await this.setName(index, preset.name);
  }

  async setSlider(slider, value) {
    const v = Math.max(0, Math.min(p.SLIDER_MAX, Math.trunc(value)));
    await this.set(Cmd.SET_SLIDER, [slider, v], slider);
    this.state.live.sliders[slider] = v;
  }

  async setNoiseGate(mode) {
    // Unlike the other setters this one echoes the mode instead of the command.
    p.expectEcho(await this.t.transact(Cmd.SET_NOISE_GATE, [mode], { retries: 2 }), mode);
    this.state.live.noiseGate = mode;
  }

  async setMicEq(mode) {
    await this.set(Cmd.SET_MIC_EQ, [mode]);
    this.state.live.micEq = mode;
  }

  async setBalance(value) {
    const v = Math.max(0, Math.min(p.BALANCE_MAX, Math.trunc(value)));
    await this.set(Cmd.SET_BALANCE, [v]);
    this.state.live.balance = v;
  }

  async setAlertVolume(value) {
    const v = Math.max(0, Math.min(p.ALERT_MAX, Math.trunc(value)));
    await this.set(Cmd.SET_ALERT_VOLUME, [v]);
    this.state.live.alertVolume = v;
  }

  async setBrightness(value) {
    const payload = p.payloadBrightness(value);
    await this.t.transact(Cmd.SET_BRIGHTNESS, payload, { retries: 2 }); // empty status-0 answer
    this.state.live.brightness = payload[0];
  }

  async setAutoOff(option) {
    const o = Math.max(0, Math.min(p.AUTO_OFF_MINUTES.length - 1, Math.trunc(option)));
    await this.set(Cmd.SET_AUTO_OFF, [p.AUTO_OFF_MINUTES[o]]);
    this.state.live.autoOff = o;
  }

  /** Persist the live settings on the headset (ACC calls this "sync"); takes ~2 s. */
  async save() {
    for (let attempt = 0; attempt < 2; attempt++) {
      const code = p.expectEcho(await this.t.transact(Cmd.SAVE, [], { timeout: 5000 }), Cmd.SAVE)[0];
      if (code === 0) {
        this.state.saved = clone(this.state.live);
        return;
      }
      if (code === SAVE_BUSY && attempt === 0) {
        await sleep(1500);
        continue;
      }
      throw new p.DeviceError(Cmd.SAVE, code ?? -1, "headset refused to save");
    }
  }

  /** Rewrite the whole saved copy into the live settings (a lost answer may hide a landed write). */
  async revert() {
    const saved = clone(this.state.saved);
    for (let i = 0; i < p.PRESET_COUNT; i++) {
      const preset = saved.presets[i];
      await this.setGains(i, preset.bands.map((b) => b.gain));
      for (let b = 0; b < p.BAND_COUNT; b++) await this.setBand(i, b, preset.bands[b].freq, preset.bands[b].bandwidth);
      if (preset.name) await this.setName(i, preset.name);
    }
    for (const [sid, value] of Object.entries(saved.sliders)) await this.setSlider(Number(sid), value);
    await this.setNoiseGate(saved.noiseGate);
    await this.setMicEq(saved.micEq);
    await this.setBalance(saved.balance);
    await this.setAlertVolume(saved.alertVolume);
    await this.setBrightness(saved.brightness);
    await this.setAutoOff(saved.autoOff);
  }
}
