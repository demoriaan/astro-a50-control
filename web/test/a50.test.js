import assert from "node:assert/strict";
import test from "node:test";

import { A50 } from "../js/device.js";
import { responseDb } from "../js/eq-math.js";
import { factoryPresets, parseIni, toIni } from "../js/presets.js";
import * as p from "../js/protocol.js";
import { JobQueue } from "../js/queue.js";
import { FakeBase } from "../js/demo-base.js";

const hexBytes = (s) => Uint8Array.from(s.trim().split(/\s+/).map((h) => parseInt(h, 16)));

test("request encoding", () => {
  assert.deepEqual([...p.encodeRequest(p.Cmd.GET_SLIDER, [4], 2).slice(0, 4)], [0x68, 0x81, 4, 0]);
  assert.deepEqual([...p.encodeRequest(p.Cmd.HEADSET_FW_MINOR, [], 1).slice(0, 3)], [0xd6, 0x41, 0x0a]);
  assert.equal(p.encodeRequest(0x62, [1, 2], 0).length, 63);
  assert.throws(() => p.encodeRequest(0x6d, new Array(62).fill(0), 0), RangeError);
});

test("response decoding and device errors", () => {
  assert.deepEqual([...p.decodeResponse(0x7c, hexBytes("02 01 4e"))], [0x4e]);
  assert.deepEqual([...p.decodeResponse(0x75, hexBytes("00 00"))], []);
  const msg = [...hexBytes("01 00 00 00"), ...new TextEncoder().encode("HID_ERROR_UNHANDLED_PACKET"), 0];
  assert.throws(() => p.decodeResponse(0x6b, Uint8Array.from([0x01, msg.length, ...msg])),
    (err) => err.name === "DeviceError" && err.deviceMessage === "HID_ERROR_UNHANDLED_PACKET" && err.code === 1);
});

test("parsers match captured reports", () => {
  const info = p.parseTxInfo(hexBytes("50 42 02 08 86 98 2c 00 e6 07 04 14 09 04 27 00 00 58 42 02 08 b4 9d 00 00 00 5c 42 02 08 68 42 02 08 78 42 02 08"));
  assert.deepEqual(info, { vendorId: 0x9886, productId: 0x2c, buildDate: "2022-04-20 09:04:39", version: 40372 });
  assert.deepEqual(p.parseEqGains(hexBytes("69 01 12 07 0c 13 13 12 07 0c 13 13"), 0)[0], [6, -5, 0, 7, 7]);
  assert.deepEqual(p.parseEqBand(hexBytes("70 02 02 00 08 00 08 69 10 69 10"), 1, 1)[0], { freq: 4201, bandwidth: 0.5 });
  assert.deepEqual(p.parseBattery(Uint8Array.of(0xe4)), { battery: 100, charging: true });
  assert.deepEqual(p.parseHeadsetStatus(Uint8Array.of(0x02)), { headsetOn: true, docked: false });
  assert.equal(p.parseAutoOff(Uint8Array.of(15)), 1);
  assert.throws(() => p.parsePair(hexBytes("68 02 51 51"), 0x68, 1), p.ProtocolError);
});

test("payloads", () => {
  assert.deepEqual(p.payloadEqGains(2, [-12, 0, 12, 13, -20]), [3, 0, 12, 24, 24, 0]);
  assert.deepEqual(p.payloadEqBand(0, 1, 406, 2.0), [1, 2, 0x00, 0x20, 0x96, 0x01]);
  assert.deepEqual(p.payloadEqName(1, "PRO"), [2, 4, 0x50, 0x52, 0x4f, 0]);
  assert.equal(p.clipName("Ä".repeat(30)), "Ä".repeat(24));
});

test("device model: read, edit, save, revert", async () => {
  const base = new FakeBase();
  const dev = new A50(base);
  const s = await dev.readAll();
  assert.equal(s.base.version, 40372);
  assert.equal(s.headsetVersion, "39964.43");
  assert.equal(s.activePreset, 2);
  assert.equal(s.live.presets[2].name, "STUDIO+");
  assert.equal(dev.dirty, false);

  await dev.setSlider(p.Slider.SIDETONE, 10);
  await dev.setBand(1, 2, 1234, 0.7);
  await dev.setNoiseGate(p.NoiseGate.TOURNAMENT);
  await dev.setBrightness(40);
  assert.equal(dev.dirty, true);
  await dev.save();
  assert.equal(dev.dirty, false);
  assert.equal(base.saved.sliders[5], 10);

  await dev.setName(0, "TEST");
  await dev.setAutoOff(4);
  base.live.balance = 3; // a write that landed although its answer was lost
  await dev.revert();
  assert.deepEqual(base.live, base.saved);
  assert.equal(dev.dirty, false);
});

test("shelf bands never get a bandwidth", async () => {
  const base = new FakeBase();
  const dev = new A50(base);
  await dev.readAll();
  await dev.setBand(0, 0, 100, 2.0);
  assert.deepEqual(base.live.bands[1][0], [0, 100]);
});

test("ini import/export round trip and ASTRO Command Center file", () => {
  const preset = factoryPresets()[0];
  assert.deepEqual(parseIni(toIni(preset)), preset);
  const acc = parseIni("[General]\nName=A50 MOD KIT\n\n[Eq_Bands]\nEq_Band_1=-5\nEq_Band_2=-7\nEq_Band_3=5\nEq_Band_4=-7\nEq_Band_5=5\n"
    + "Eq_Freq_Band_1=200\nEq_Freq_Band_2=325\nEq_Freq_Band_3=2753\nEq_Freq_Band_4=6691\nEq_Freq_Band_5=11002\n"
    + "Eq_Bandwidth_2=1.7\nEq_Bandwidth_3=2.0\nEq_Bandwidth_4=0.5\n");
  assert.equal(acc.name, "A50 MOD KIT");
  assert.deepEqual(acc.bands.map((b) => b.bandwidth), [0, 1.7, 2, 0.5, 0]);
  assert.throws(() => parseIni("Name=x"));
  assert.throws(() => parseIni("[Eq_Bands]\nEq_Band_1=abc"));
  // no prototype pollution from crafted section names
  assert.throws(() => parseIni("[__proto__]\ntimeout=0\n[constructor]\nassign=1"));
  assert.equal(({}).timeout, undefined);
  assert.equal(typeof Object.assign, "function");
  // names cannot inject lines into exported files
  const evil = { ...preset, name: "x\n[Eq_Bands]\nEq_Band_1=12" };
  assert.equal(parseIni(toIni(evil)).bands[0].gain, preset.bands[0].gain);
});

test("queue: replaced job moves to the end", async () => {
  const q = new JobQueue();
  const order = [];
  let release;
  const gate = new Promise((r) => { release = r; });
  q.submit("block", () => gate);
  const old = q.submit("gains", () => order.push("gains-old"));
  q.submit("apply", () => order.push("apply"));
  q.submit("gains", () => order.push("gains-new"));
  release();
  await q.idle();
  assert.deepEqual(order, ["apply", "gains-new"]);
  assert.equal(await old, undefined);
});

test("eq math: flat and peak", () => {
  const flat = [100, 300, 1000, 3000, 8000].map((freq) => ({ gain: 0, freq, bandwidth: 1 }));
  assert.ok(responseDb(flat, [50, 1000, 10000]).every((v) => Math.abs(v) < 1e-9));
  const peak = flat.map((b, i) => ({ ...b, gain: i === 2 ? 6 : 0 }));
  assert.ok(Math.abs(responseDb(peak, [1000])[0] - 6) < 0.05);
});
