// WebHID transport: one request at a time, responses matched by the 2-bit sequence number.
import { decodeResponse, encodeRequest, hex, PRODUCT_ID, REPORT_ID, responseSeq, USAGE_PAGE, VENDOR_ID } from "./protocol.js";

export class TimeoutError extends Error {
  name = "TimeoutError";
}
export class DeviceGoneError extends Error {
  name = "DeviceGoneError";
}

export const DEVICE_FILTERS = [{ vendorId: VENDOR_ID, productId: PRODUCT_ID, usagePage: USAGE_PAGE }];

export function hidSupported() {
  return typeof navigator !== "undefined" && "hid" in navigator;
}

/** Ask the user to pick the base station (needs a user gesture). */
export async function requestDevice() {
  const devices = await navigator.hid.requestDevice({ filters: DEVICE_FILTERS });
  return devices[0] ?? null;
}

/** A base station this origin was already granted, if plugged in. */
export async function grantedDevice() {
  const devices = await navigator.hid.getDevices();
  return devices.find((d) => d.vendorId === VENDOR_ID && d.productId === PRODUCT_ID
    && d.collections.some((c) => c.usagePage === USAGE_PAGE)) ?? null;
}

export class WebHidTransport {
  constructor(device) {
    this.device = device;
    this.seq = 0;
    this.pending = null;
    this.chain = Promise.resolve();
    this.closed = false;
    this.onReport = (event) => this.handleReport(event);
    device.addEventListener("inputreport", this.onReport);
  }

  static async open(device) {
    if (!device.opened) await device.open();
    return new WebHidTransport(device);
  }

  async close() {
    this.closed = true;
    this.device.removeEventListener("inputreport", this.onReport);
    if (this.pending) this.pending.reject(new DeviceGoneError("transport closed"));
    try {
      if (this.device.opened) await this.device.close();
    } catch {
      // already gone
    }
  }

  handleReport(event) {
    if (event.reportId !== REPORT_ID || !this.pending) return;
    const report = new Uint8Array(event.data.buffer, event.data.byteOffset, event.data.byteLength);
    if (responseSeq(report) === this.pending.seq) this.pending.resolve(report.slice());
  }

  /** Send one command; resolves with the response data bytes. */
  transact(cmd, payload = [], { timeout = 1000, retries = 1 } = {}) {
    const run = () => this.exchange(cmd, payload, timeout, retries);
    const result = this.chain.then(run, run);
    this.chain = result.catch(() => {});
    return result;
  }

  async exchange(cmd, payload, timeout, retries) {
    for (let attempt = 0; attempt <= retries; attempt++) {
      if (this.closed) throw new DeviceGoneError("transport closed");
      const seq = this.seq;
      this.seq = (this.seq + 1) & 0x03;
      const reply = new Promise((resolve, reject) => {
        this.pending = { seq, resolve, reject };
      });
      try {
        await this.device.sendReport(REPORT_ID, encodeRequest(cmd, payload, seq));
      } catch (err) {
        this.pending = null;
        throw new DeviceGoneError(`send failed: ${err.message}`);
      }
      let timer;
      const expired = new Promise((resolve) => { timer = setTimeout(() => resolve(null), timeout); });
      const report = await Promise.race([reply, expired]);
      clearTimeout(timer);
      this.pending = null;
      if (report) return decodeResponse(cmd, report);
    }
    throw new TimeoutError(`no response to command 0x${hex(cmd)}`);
  }
}
