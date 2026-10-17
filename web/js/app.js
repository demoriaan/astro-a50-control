// Page controller: connection lifecycle, polling, coalesced writes and rendering.
import { A50, clone, sameSettings } from "./device.js";
import { EqGraph } from "./eq-graph.js";
import { lang, setLang, t, translatePage } from "./i18n.js";
import { buildBands, buildDevicePanel, buildMicPanel, buildStreamPanel, el } from "./panels.js";
import { factoryPresets, parseIni, toIni } from "./presets.js";
import * as p from "./protocol.js";
import { JobQueue } from "./queue.js";
import { ForeignTrafficError, grantedDevice, hidSupported, requestDevice, WebHidTransport } from "./transport.js";

const DEMO = new URLSearchParams(location.search).has("demo");

const POLL_MS = 2000;
const POLL_FAILURES_BEFORE_RESET = 3;
const LOCK_NAME = "a50-base-station";
const $ = (id) => document.getElementById(id);

const app = {
  dev: null, hid: null, state: null, queue: new JobQueue(),
  connecting: false, writes: 0, saving: false, pollFailures: 0, presetGen: 0, selected: 0, releaseLock: null,
  closing: null,
};

// -- feedback ---------------------------------------------------------------

let toastTimer;
function toast(text) {
  const node = $("toast");
  node.textContent = text;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

// Messages are kept as functions so a language switch can re-render the current one.
let message = null;
function showMessage(text) {
  message = text;
  renderMessage();
}

function renderMessage() {
  const node = $("connect-message");
  node.hidden = !message;
  node.textContent = message ? message() : "";
}

function errorText(err) {
  if (err?.name === "TimeoutError") return t("noAnswer");
  if (err?.name === "DeviceError") return t("deviceSays", { message: err.deviceMessage });
  return t("error", { message: err?.message ?? err });
}

// -- connection -------------------------------------------------------------

/** Hold an exclusive Web Lock while connected so a second tab of this browser cannot connect. */
function acquireLock() {
  const noLock = () => {};
  if (DEMO || !navigator.locks) return Promise.resolve(noLock);
  return new Promise((resolve) => {
    // Rejected when site storage is blocked; then rely on the stray-report detection alone.
    navigator.locks.request(LOCK_NAME, { ifAvailable: true }, (lock) => {
      if (!lock) {
        resolve(null);
        return undefined;
      }
      return new Promise((release) => resolve(release));
    }).catch(() => resolve(noLock));
  });
}

/** True if another tab of this browser holds the device. */
async function lockedElsewhere() {
  if (DEMO || !navigator.locks || app.dev || app.closing) return false;
  try {
    const { held } = await navigator.locks.query();
    return held.some((l) => l.name === LOCK_NAME);
  } catch {
    return false;
  }
}

const TAB_MESSAGE = () => t("otherTab");
const FOREIGN_MESSAGE = () => t("foreignTraffic");

async function connect(device) {
  if (app.connecting || app.dev || !device) return;
  app.connecting = true;
  $("connect").disabled = true;
  showMessage(null);
  if (app.closing) await app.closing; // our own previous connection must be fully closed
  const release = await acquireLock();
  if (!release) {
    showMessage(TAB_MESSAGE);
    app.connecting = false;
    $("connect").disabled = false;
    return;
  }
  let dev;
  let foreign = false;
  try {
    if (DEMO) {
      const { FakeBase } = await import("./demo-base.js");
      dev = new A50(new FakeBase());
    } else {
      dev = new A50(await WebHidTransport.open(device, {
        onForeign: () => { foreign = true; onForeignTraffic(dev); },
      }));
    }
  } catch (err) {
    release();
    showMessage(() => t("openFailed", { message: err.message }));
    app.connecting = false;
    $("connect").disabled = false;
    return;
  }
  try {
    const state = await dev.readAll();
    if (foreign) throw new ForeignTrafficError("other client detected while reading");
    app.dev = dev;
    app.hid = device;
    app.releaseLock = release;
    app.state = clone(state);
    app.pollFailures = 0;
    showApp(true);
    renderAll({ cancelDrag: true });
    toast(t(DEMO ? "connectedDemo" : "connected"));
  } catch (err) {
    await dev.close();
    release();
    showMessage(foreign || err?.name === "ForeignTrafficError" ? FOREIGN_MESSAGE
      : () => t("retryHint", { error: errorText(err) }));
  } finally {
    app.connecting = false;
    $("connect").disabled = false;
  }
}

async function disconnect(message) {
  const dev = app.dev;
  const release = app.releaseLock;
  app.dev = null;
  app.hid = null;
  app.state = null;
  app.releaseLock = null;
  app.queue.clear();
  showApp(false);
  showMessage(message);
  app.closing = (async () => {
    if (dev) await dev.close();
    if (release) release();
  })();
  await app.closing;
  app.closing = null;
}

function onForeignTraffic(dev) {
  if (dev && dev === app.dev) disconnect(FOREIGN_MESSAGE);
}

function showApp(connected) {
  $("welcome").hidden = connected;
  $("app").hidden = !connected;
  $("status").hidden = !connected;
  $("actions").hidden = !connected;
}

// -- device jobs ------------------------------------------------------------

function run(key, job) {
  const dev = app.dev;
  if (!dev) return;
  app.writes++;
  app.queue.submit(key, () => job(dev)).catch((err) => onWriteError(err, dev));
}

function onWriteError(err, dev) {
  if (dev !== app.dev) return;
  if (err?.name === "DeviceGoneError") {
    disconnect(() => t("connectionLost"));
    return;
  }
  toast(errorText(err));
  resync();
}

function onPollError(err, dev) {
  if (dev !== app.dev) return;
  if (err?.name === "DeviceGoneError") {
    disconnect(() => t("connectionLost"));
  } else if (err?.name === "TimeoutError" && ++app.pollFailures >= POLL_FAILURES_BEFORE_RESET) {
    disconnect(() => t("notResponding"));
  }
}

/** Re-read everything; re-runs if an edit was queued meanwhile so it never shows stale values. */
function resync(done) {
  const dev = app.dev;
  if (!dev) return;
  const writes = app.writes;
  app.queue.submit("refresh", async () => clone(await dev.readAll())).then((state) => {
    if (!state || dev !== app.dev) return;
    if (writes !== app.writes) {
      resync(done);
      return;
    }
    app.state = state;
    renderAll({ cancelDrag: true });
    if (done) toast(done);
  }, (err) => onPollError(err, dev));
}

function poll() {
  const dev = app.dev;
  if (!dev || app.queue.pending("poll") || document.hidden) return;
  const presetGen = app.presetGen;
  app.queue.submit("poll", async () => clone(await dev.readStatus())).then((status) => {
    if (!status || dev !== app.dev) return;
    app.pollFailures = 0;
    const s = app.state;
    for (const key of ["battery", "charging", "mix", "headsetOn", "docked", "headsetVersion"]) s[key] = status[key];
    // Only adopt the headset's preset if the user did not switch since this poll was queued.
    if (presetGen === app.presetGen && status.activePreset !== s.activePreset) {
      s.activePreset = status.activePreset;
      renderEq({ cancelDrag: true });
    }
    renderStatus();
  }, (err) => onPollError(err, dev));
}

function save() {
  const dev = app.dev;
  if (!dev) return;
  app.saving = true;
  renderDirty();
  app.queue.submit("save", async () => { await dev.save(); return clone(dev.state.saved); }).then((saved) => {
    app.saving = false;
    if (saved && dev === app.dev) {
      app.state.saved = saved;
      toast(t("savedToast"));
    }
    if (app.state) renderDirty();
  }, (err) => {
    app.saving = false;
    onWriteError(err, dev);
    renderDirty();
  });
}

function revert() {
  const dev = app.dev;
  if (!dev) return;
  const writes = app.writes;
  app.queue.submit("revert", async () => { await dev.revert(); return clone(dev.state); }).then((state) => {
    if (!state || dev !== app.dev) return;
    if (writes !== app.writes) {
      resync(t("reverted"));
      return;
    }
    app.state = state;
    renderAll({ cancelDrag: true });
    toast(t("reverted"));
  }, (err) => onWriteError(err, dev));
}

// -- edits ------------------------------------------------------------------

const live = () => app.state.live;
const activePreset = () => live().presets[app.state.activePreset];

function editBand(i, change) {
  const index = app.state.activePreset;
  const band = activePreset().bands[i];
  const before = { ...band };
  const has = (key) => Object.hasOwn(change, key) && Number.isFinite(change[key]);
  if (has("gain")) band.gain = Math.max(p.GAIN_MIN, Math.min(p.GAIN_MAX, change.gain));
  if (has("freq")) band.freq = Math.max(p.FREQ_MIN, Math.min(p.FREQ_MAX, change.freq));
  if (has("bandwidth") && p.PEAKING_BANDS.includes(i)) band.bandwidth = Math.max(p.BW_MIN, Math.min(p.BW_MAX, change.bandwidth));
  app.selected = i;
  if (band.gain !== before.gain) {
    const gains = activePreset().bands.map((b) => b.gain);
    run(`gains-${index}`, (d) => d.setGains(index, gains));
  }
  if (band.freq !== before.freq || band.bandwidth !== before.bandwidth) {
    const { freq, bandwidth } = band;
    run(`band-${index}-${i}`, (d) => d.setBand(index, i, freq, bandwidth));
  }
  renderEq();
  renderDirty();
}

function activate(index) {
  app.state.activePreset = index;
  app.presetGen++;
  renderEq({ cancelDrag: true });
  run("preset", (d) => d.setActivePreset(index));
}

function applyPreset(preset) {
  const index = app.state.activePreset;
  const next = { name: p.clipName(preset.name || activePreset().name), bands: clone(preset.bands) };
  live().presets[index] = next;
  const snapshot = clone(next);
  run(`apply-${index}`, (d) => d.applyPreset(index, snapshot));
  renderEq({ cancelDrag: true });
  renderDirty();
  toast(t("presetLoaded", { name: next.name }));
}

function setting(key, value, job) {
  live()[key] = value;
  run(key, job);
  renderDirty();
}

const actions = {
  slider: (slider, value) => {
    live().sliders[slider] = value;
    run(`slider-${slider}`, (d) => d.setSlider(slider, value));
    renderDirty();
  },
  noiseGate: (mode) => setting("noiseGate", mode, (d) => d.setNoiseGate(mode)),
  micEq: (mode) => setting("micEq", mode, (d) => d.setMicEq(mode)),
  balance: (v) => setting("balance", v, (d) => d.setBalance(v)),
  alertVolume: (v) => setting("alertVolume", v, (d) => d.setAlertVolume(v)),
  brightness: (v) => setting("brightness", v, (d) => d.setBrightness(v)),
  autoOff: (o) => setting("autoOff", o, (d) => d.setAutoOff(o)),
};

// -- rendering --------------------------------------------------------------

const graph = new EqGraph($("eq-canvas"), {
  onChange: (i, band) => editBand(i, band),
  onSelect: (i) => { app.selected = i; renderEq(); },
});
let renderBands;
let renderMic;
let renderStream;
let device;

/** (Re)build the generated controls in the current language. */
function buildPanels() {
  const panels = ["bands", "panel-mic", "panel-stream", "panel-device"].map($);
  panels.forEach((node) => node.replaceChildren());
  const [bands, mic, stream, dev] = panels;
  renderBands = buildBands(bands, {
    onEdit: editBand,
    onSelect: (i) => { if (i !== app.selected) { app.selected = i; graph.select(i); renderBands(activePreset().bands, i); } },
  });
  renderMic = buildMicPanel(mic, actions);
  renderStream = buildStreamPanel(stream, actions);
  device = buildDevicePanel(dev, actions);
}

const slotButtons = Array.from({ length: p.PRESET_COUNT }, (_, i) => {
  const btn = el("button", { type: "button", role: "radio", onclick: () => activate(i) });
  btn.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const next = (i + step + p.PRESET_COUNT) % p.PRESET_COUNT;
    activate(next);
    slotButtons[next].focus();
  });
  $("slots").append(btn);
  return btn;
});

function renderEq({ cancelDrag = false } = {}) {
  const s = app.state;
  slotButtons.forEach((btn, i) => {
    btn.textContent = s.live.presets[i].name || `EQ ${i + 1}`;
    btn.setAttribute("aria-checked", String(i === s.activePreset));
    btn.tabIndex = i === s.activePreset ? 0 : -1;
  });
  graph.setBands(activePreset().bands, { cancelDrag });
  graph.select(app.selected);
  renderBands(activePreset().bands, app.selected);
  if (document.activeElement !== $("preset-name")) $("preset-name").value = activePreset().name;
}

function renderStatus() {
  const s = app.state;
  const chip = $("headset-chip");
  chip.textContent = DEMO ? "Demo" : t(!s.headsetOn ? "chipOff" : s.docked ? "chipDocked" : "chipOn");
  chip.classList.toggle("on", s.headsetOn);
  const battery = $("battery-chip");
  battery.hidden = !s.headsetOn;
  battery.classList.toggle("low", s.battery < 15 && !s.charging);
  battery.classList.toggle("charging", s.charging);
  $("battery-fill").style.width = `calc(${s.battery}% - 3px)`;
  $("battery-text").textContent = `${s.battery} %`;
  battery.title = t(s.charging ? "batteryCharging" : "batteryTitle");
  device.status(s);
}

function renderDirty() {
  const dirty = Boolean(app.state) && !sameSettings(app.state.live, app.state.saved);
  $("save").disabled = !dirty || app.saving;
  $("revert").disabled = !dirty;
}

function renderAll(opts) {
  renderEq(opts);
  renderMic(app.state);
  renderStream(app.state);
  device.all(app.state);
  renderStatus();
  renderDirty();
}

// -- wiring -----------------------------------------------------------------

function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
    }
    if (tab.id === "tab-eq") graph.resize();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      const target = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (target === undefined) return;
      e.preventDefault();
      const next = tabs[(target + tabs.length) % tabs.length];
      select(next);
      next.focus();
    });
  });
}

function setupLibrary() {
  const presets = factoryPresets();
  const select = $("factory");
  presets.forEach((preset, i) => select.append(el("option", { value: i, text: preset.name })));
  // Applying writes to the headset, so it needs an explicit button (arrow keys on a
  // closed <select> would otherwise fire "change" for every step).
  $("load-factory").addEventListener("click", () => {
    if (select.value !== "") applyPreset(presets[Number(select.value)]);
  });
  $("import").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      applyPreset(parseIni(await file.text(), file.name.replace(/\.[^.]+$/, "")));
    } catch (err) {
      toast(t("importFailed", { message: err.message }));
    }
  });
  $("export").addEventListener("click", () => {
    const preset = activePreset();
    const url = URL.createObjectURL(new Blob([toIni(preset)], { type: "text/plain" }));
    const link = el("a", { href: url, download: `${preset.name || "preset"}.ini` });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(t("exported", { name: preset.name }));
  });
  $("rename-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = p.clipName($("preset-name").value.trim());
    if (!name) return;
    const index = app.state.activePreset;
    activePreset().name = name;
    $("preset-name").value = name;
    run(`name-${index}`, (d) => d.setName(index, name));
    renderEq();
    renderDirty();
  });
}

/** Apply the current language to the static page and every generated control. */
function localize() {
  translatePage();
  if (DEMO) document.title = `Demo – ${document.title}`;
  buildPanels();
  if (app.state) renderAll();
  renderMessage();
}

function init() {
  const preview = new EqGraph($("welcome-canvas"), { onChange() {}, onSelect() {} });
  preview.setBands(factoryPresets()[0].bands);
  preview.select(-1);
  localize();
  $("lang").addEventListener("click", () => {
    setLang(lang === "de" ? "en" : "de");
    localize();
  });
  setupTabs();
  setupLibrary();
  $("save").addEventListener("click", save);
  $("revert").addEventListener("click", revert);
  $("copy-udev").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("udev-rule").textContent);
      toast(t("copied"));
    } catch {
      toast(t("copyFailed"));
    }
  });
  window.addEventListener("beforeunload", (e) => {
    if (app.state && !sameSettings(app.state.live, app.state.saved)) e.preventDefault();
  });
  if (DEMO) {
    connect({ demo: true });
    return;
  }
  if (!hidSupported()) {
    $("connect").disabled = true;
    showMessage(() => t("noWebHid"));
    return;
  }
  $("connect").addEventListener("click", async () => {
    if (await lockedElsewhere()) {
      showMessage(TAB_MESSAGE);
      return;
    }
    try {
      await connect(await requestDevice());
    } catch (err) {
      showMessage(() => t("pickFailed", { message: err.message }));
    }
  });
  navigator.hid.addEventListener("disconnect", (e) => {
    if (e.device === app.hid) disconnect(() => t("unplugged"));
  });
  // Never auto-connect inside a frame: a hostile page could embed this one with allow="hid".
  const autoConnect = window.top === window;
  navigator.hid.addEventListener("connect", () => { if (!app.dev && autoConnect) grantedDevice().then(connect); });
  setInterval(poll, POLL_MS);
  if (autoConnect) grantedDevice().then(connect);
}

init();
