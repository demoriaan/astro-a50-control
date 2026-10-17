// Wire protocol of the ASTRO A50 Gen 4 base station (vendor HID page 0xFF32, report ID 2).
// Port of a50control/protocol.py; the evidence for every command is in docs/PROTOCOL.md.
//
//   request : [cmd, seq << 6 | len, payload...]     (63 bytes, report ID 2 sent separately)
//   response: [status, seq << 6 | len, data...]     (status 0x02 ok, 0x00 ok-without-data, 0x01 error)
//
// Commands with bit 7 set are relayed to the headset and need a 0x0A prefix byte.

export const VENDOR_ID = 0x9886;
export const PRODUCT_ID = 0x002c;
export const USAGE_PAGE = 0xff32;
export const REPORT_ID = 0x02;
export const REPORT_DATA_SIZE = 63;
export const MAX_PAYLOAD = REPORT_DATA_SIZE - 2;
const STATUS_OK = 0x02;
const STATUS_EMPTY = 0x00;
const REMOTE_PREFIX = 0x0a;

export const Cmd = Object.freeze({
  TX_INFO: 0x03,
  HEADSET_STATUS: 0x54,
  BASE_FW_MINOR: 0x55,
  SAVE: 0x61,
  SET_SLIDER: 0x62,
  SET_EQ_GAINS: 0x63,
  SET_NOISE_GATE: 0x64,
  SET_ACTIVE_EQ: 0x67,
  GET_SLIDER: 0x68,
  GET_EQ_GAINS: 0x69,
  GET_NOISE_GATE: 0x6a,
  GET_ACTIVE_EQ: 0x6c,
  SET_EQ_NAME: 0x6d,
  GET_EQ_NAME: 0x6e,
  SET_EQ_BAND: 0x6f,
  GET_EQ_BAND: 0x70,
  SET_MIC_EQ: 0x71,
  GET_MIX: 0x72,
  SET_BALANCE: 0x73,
  SET_AUTO_OFF: 0x74,
  SET_BRIGHTNESS: 0x75,
  SET_ALERT_VOLUME: 0x76,
  GET_BALANCE: 0x77,
  GET_AUTO_OFF: 0x78,
  GET_BRIGHTNESS: 0x79,
  GET_ALERT_VOLUME: 0x7a,
  GET_MIC_EQ: 0x7b,
  GET_BATTERY: 0x7c,
  HEADSET_FW_MINOR: 0xd6,
  HEADSET_FW_MAJOR: 0xda,
});

export const Slider = Object.freeze({
  STREAM_MIC: 0, STREAM_CHAT: 1, STREAM_GAME: 2, STREAM_AUX: 3, MIC_LEVEL: 4, SIDETONE: 5,
});
export const NoiseGate = Object.freeze({ STREAMING: 0, NIGHT: 1, HOME: 2, TOURNAMENT: 3 });
export const MicEq = Object.freeze({ BRIGHT: 0, AIRY: 1, WARM: 2 });

export const PRESET_COUNT = 3;
export const BAND_COUNT = 5;
export const GAIN_MIN = -12;
export const GAIN_MAX = 12;
export const FREQ_MIN = 80;
export const FREQ_MAX = 15000;
export const BW_MIN = 0.1;
export const BW_MAX = 3.0;
export const NAME_MAX = 49;
export const SLIDER_MAX = 100;
export const BALANCE_MAX = 255; // 0 = all chat/voice, 255 = all game (verified by audio test)
export const ALERT_MAX = 100;
export const BRIGHTNESS_MIN = 20;
export const BRIGHTNESS_MAX = 70;
export const BRIGHTNESS_ZONES = 9;
export const AUTO_OFF_MINUTES = Object.freeze([10, 15, 20, 25, 30]);
export const PEAKING_BANDS = Object.freeze([1, 2, 3]); // zero-based; bands 0 and 4 are shelves
const GAIN_OFFSET = 12;
const BW_SCALE = 4096;

export class ProtocolError extends Error {
  name = "ProtocolError";
}

export class DeviceError extends Error {
  name = "DeviceError";

  constructor(cmd, code, message) {
    super(`command 0x${hex(cmd)} failed: ${message} (code 0x${code.toString(16).padStart(8, "0")})`);
    this.cmd = cmd;
    this.code = code;
    this.deviceMessage = message;
  }
}

export const hex = (n) => n.toString(16).padStart(2, "0");
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function encodeRequest(cmd, payload, seq) {
  let body = Array.from(payload ?? []);
  if (cmd & 0x80) body = [REMOTE_PREFIX, ...body];
  if (body.length > MAX_PAYLOAD) throw new RangeError(`payload too long (${body.length} > ${MAX_PAYLOAD})`);
  const out = new Uint8Array(REPORT_DATA_SIZE);
  out[0] = cmd & 0xff;
  out[1] = ((seq & 0x03) << 6) | body.length;
  out.set(body, 2);
  return out;
}

export const responseSeq = (report) => report[1] >> 6;

export function decodeResponse(cmd, report) {
  if (report.length < 2) throw new ProtocolError("short response");
  const status = report[0];
  const data = report.slice(2, 2 + (report[1] & 0x3f));
  if (status === STATUS_OK || status === STATUS_EMPTY) return data;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const code = data.length >= 4 ? view.getUint32(0, true) : -1;
  const message = cString(data.slice(4)) || `status 0x${hex(status)}`;
  throw new DeviceError(cmd, code, message);
}

export function expectEcho(data, ...prefix) {
  for (let i = 0; i < prefix.length; i++) {
    if (data[i] !== prefix[i]) {
      throw new ProtocolError(`echo mismatch: want ${prefix.map(hex).join("")} got ${Array.from(data.slice(0, prefix.length), hex).join("")}`);
    }
  }
  return data.slice(prefix.length);
}

function cString(bytes) {
  const end = bytes.indexOf(0);
  return new TextDecoder().decode(end >= 0 ? bytes.slice(0, end) : bytes);
}

const u16 = (b, o) => b[o] | (b[o + 1] << 8);
const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;

export const gainToRaw = (gain) => clamp(Math.trunc(gain), GAIN_MIN, GAIN_MAX) + GAIN_OFFSET;
export const rawToGain = (raw) => raw - GAIN_OFFSET;
export const bwToRaw = (bw) => Math.round(bw * BW_SCALE) & 0xffff;
export const rawToBw = (raw) => Math.round((raw / BW_SCALE) * 10) / 10;

export function parsePair(data, cmd, ...params) {
  const rest = expectEcho(data, cmd, ...params);
  if (rest.length < 2) throw new ProtocolError(`short response for 0x${hex(cmd)}`);
  return [rest[0], rest[1]];
}

export function parseEqGains(data, preset) {
  const rest = expectEcho(data, Cmd.GET_EQ_GAINS, preset + 1);
  if (rest.length < 2 * BAND_COUNT) throw new ProtocolError("short EQ gain response");
  return [Array.from(rest.slice(0, BAND_COUNT), rawToGain), Array.from(rest.slice(BAND_COUNT, 2 * BAND_COUNT), rawToGain)];
}

export function parseEqBand(data, preset, band) {
  const rest = expectEcho(data, Cmd.GET_EQ_BAND, preset + 1, band + 1);
  if (rest.length < 8) throw new ProtocolError("short EQ band response");
  return [
    { freq: u16(rest, 4), bandwidth: rawToBw(u16(rest, 0)) },
    { freq: u16(rest, 6), bandwidth: rawToBw(u16(rest, 2)) },
  ];
}

export const parseEqName = (data, preset) => cString(expectEcho(data, Cmd.GET_EQ_NAME, preset + 1));

export function parseNoiseGate(data) {
  const rest = expectEcho(data, Cmd.GET_NOISE_GATE);
  if (rest.length < 2) throw new ProtocolError("unsupported noise gate response");
  return [rest[0], rest[1]];
}

export function parseBattery(data) {
  if (!data.length) throw new ProtocolError("empty battery response");
  return { battery: Math.min(data[0] & 0x7f, 100), charging: Boolean(data[0] & 0x80) };
}

export function parseHeadsetStatus(data) {
  const raw = data.length ? data[0] : 0;
  return { headsetOn: Boolean(raw & 0x02), docked: Boolean(raw & 0x01) };
}

export function parseBrightness(data) {
  const zones = data.length ? data.slice(0, BRIGHTNESS_ZONES) : [0];
  return Math.round(zones.reduce((a, b) => a + b, 0) / zones.length);
}

export function parseAutoOff(data) {
  if (!data.length) throw new ProtocolError("empty auto-off response");
  let best = 0;
  AUTO_OFF_MINUTES.forEach((m, i) => {
    if (Math.abs(m - data[0]) < Math.abs(AUTO_OFF_MINUTES[best] - data[0])) best = i;
  });
  return best;
}

export function parseU32(data) {
  if (data.length < 4) throw new ProtocolError("short u32 response");
  return u32(data, 0);
}

export function parseTxInfo(data) {
  if (data.length < 0x19) throw new ProtocolError("short info response");
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return {
    vendorId: u16(data, 4),
    productId: u16(data, 6),
    buildDate: `${pad(u16(data, 8), 4)}-${pad(data[10])}-${pad(data[11])} ${pad(data[12])}:${pad(data[13])}:${pad(data[14])}`,
    version: u32(data, 0x15),
  };
}

/** Shorten to NAME_MAX UTF-8 bytes without splitting a character. */
export function clipName(name) {
  let out = "";
  let bytes = 0;
  for (const ch of name) {
    const size = new TextEncoder().encode(ch).length;
    if (bytes + size > NAME_MAX) break;
    out += ch;
    bytes += size;
  }
  return out;
}

export const payloadEqGains = (preset, gains) => [preset + 1, ...gains.map(gainToRaw)];

export function payloadEqBand(preset, band, freq, bandwidth) {
  const f = clamp(Math.trunc(freq), FREQ_MIN, FREQ_MAX);
  const bw = bwToRaw(bandwidth);
  return [preset + 1, band + 1, bw & 0xff, bw >> 8, f & 0xff, f >> 8];
}

export function payloadEqName(preset, name) {
  const raw = new TextEncoder().encode(clipName(name));
  if (!raw.length) throw new RangeError("preset name must not be empty");
  return [preset + 1, raw.length + 1, ...raw, 0];
}

export function payloadBrightness(level) {
  return new Array(BRIGHTNESS_ZONES).fill(clamp(Math.trunc(level), BRIGHTNESS_MIN, BRIGHTNESS_MAX));
}
