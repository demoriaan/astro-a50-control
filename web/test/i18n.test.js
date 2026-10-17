import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { pickLang, strings } from "../js/i18n.js";

const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

test("German and English define the same keys and placeholders", () => {
  assert.deepEqual(Object.keys(strings.de).sort(), Object.keys(strings.en).sort());
  for (const [key, text] of Object.entries(strings.en)) {
    assert.deepEqual(placeholders(strings.de[key]), placeholders(text), key);
  }
});

test("every key used by index.html exists", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const keys = [...html.matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]);
  for (const attrs of html.matchAll(/data-i18n-attr="([^"]+)"/g)) {
    keys.push(...attrs[1].split(";").map((pair) => pair.split(":")[1]));
  }
  assert.ok(keys.length > 20);
  for (const key of keys) assert.ok(Object.hasOwn(strings.en, key), key);
});

test("German only when the preferred language is German, unless chosen", () => {
  assert.equal(pickLang(null, ["de-DE", "en"]), "de");
  assert.equal(pickLang(null, ["de"]), "de");
  assert.equal(pickLang(null, ["DE-at"]), "de");
  assert.equal(pickLang(null, ["en-US", "de"]), "en");
  assert.equal(pickLang(null, ["fr-FR"]), "en");
  assert.equal(pickLang(null, []), "en");
  assert.equal(pickLang(null, [undefined]), "en");
  assert.equal(pickLang("de", ["en-US"]), "de");
  assert.equal(pickLang("en", ["de-DE"]), "en");
  assert.equal(pickLang("xx", ["de-DE"]), "de");
});
