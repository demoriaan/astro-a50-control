import assert from "node:assert/strict";
import test from "node:test";

import { WebHidTransport } from "../js/transport.js";

/** Minimal HIDDevice stand-in: replies via `respond(request) -> report bytes | null`. */
class FakeHid extends EventTarget {
  constructor(respond) {
    super();
    this.opened = true;
    this.respond = respond;
    this.sent = [];
  }

  async open() { this.opened = true; }

  async close() { this.opened = false; }

  async sendReport(reportId, data) {
    if (!this.opened) throw new Error("closed");
    this.sent.push([reportId, Array.from(data.slice(0, 4))]);
    const reply = this.respond(data);
    if (reply) setTimeout(() => this.emit(reply), 1);
  }

  emit(bytes) {
    const ev = new Event("inputreport");
    ev.reportId = 2;
    ev.data = new DataView(Uint8Array.from(bytes).buffer);
    this.dispatchEvent(ev);
  }
}

// Echo-style answer: status ok, same seq, data = [cmd].
const ok = (req) => [0x02, (req[1] & 0xc0) | 1, req[0]];

test("matches responses by sequence number and serializes requests", async () => {
  const hid = new FakeHid(ok);
  const t = new WebHidTransport(hid);
  const results = await Promise.all([t.transact(0x7c), t.transact(0x72), t.transact(0x6c)]);
  assert.deepEqual(results.map((r) => r[0]), [0x7c, 0x72, 0x6c]);
  assert.deepEqual(hid.sent.map(([id, d]) => [id, d[0], d[1] >> 6]), [[2, 0x7c, 0], [2, 0x72, 1], [2, 0x6c, 2]]);
});

test("ignores a reply with the wrong sequence and retries after a timeout", async () => {
  let calls = 0;
  const hid = new FakeHid((req) => {
    calls++;
    return calls === 1 ? [0x02, ((req[1] + 0x40) & 0xc0) | 1, 0xaa] : ok(req);
  });
  const t = new WebHidTransport(hid);
  const data = await t.transact(0x7c, [], { timeout: 30, retries: 1 });
  assert.equal(data[0], 0x7c);
  assert.equal(calls, 2);
});

test("times out without retries left", async () => {
  const t = new WebHidTransport(new FakeHid(() => null));
  await assert.rejects(t.transact(0x7c, [], { timeout: 20, retries: 0 }), { name: "TimeoutError" });
});

test("reports foreign traffic after two stray reports", async () => {
  let foreign = 0;
  const hid = new FakeHid(ok);
  const t = new WebHidTransport(hid, { onForeign: () => foreign++ });
  await t.transact(0x7c);
  hid.emit([0x02, 0x01, 0x55]);
  assert.equal(foreign, 0);
  hid.emit([0x02, 0x01, 0x55]);
  assert.equal(foreign, 1);
});

test("close rejects a pending request without unhandled rejections", async () => {
  const t = new WebHidTransport(new FakeHid(() => null));
  const pending = t.transact(0x7c, [], { timeout: 1000, retries: 0 });
  await new Promise((r) => setTimeout(r, 5));
  await t.close();
  await assert.rejects(pending, { name: "DeviceGoneError" });
  await assert.rejects(t.transact(0x7c), { name: "DeviceGoneError" });
});

test("device errors surface as DeviceError", async () => {
  const msg = [1, 0, 0, 0, ...new TextEncoder().encode("HID_ERROR_UNHANDLED_PACKET"), 0];
  const t = new WebHidTransport(new FakeHid((req) => [0x01, (req[1] & 0xc0) | msg.length, ...msg]));
  await assert.rejects(t.transact(0x6b), (err) => err.name === "DeviceError" && err.deviceMessage === "HID_ERROR_UNHANDLED_PACKET");
});
