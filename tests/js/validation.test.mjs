// Unit tests for the browser's pure helpers. Run with: node --test tests/js
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_MEMBERS,
  errorMessage,
  pick,
  placeLabel,
  safeLabel,
  validateHousehold,
} from "../../static/js/validation.js";

const delhi = { latitude: 28.6, longitude: 77.2, label: "Delhi" };

test("placeLabel joins known parts and caps length", () => {
  assert.equal(placeLabel({ name: "Delhi", admin1: null, country: "India" }), "Delhi, India");
  assert.equal(placeLabel({ name: "x".repeat(100) }).length, 80);
});

test("safeLabel strips characters the server rejects", () => {
  assert.equal(safeLabel("Delhi <script>"), "Delhi script");
  assert.equal(safeLabel("São Paulo, Brasil"), "São Paulo, Brasil");
  assert.equal(safeLabel("<>"), "Selected place");
});

test("validateHousehold requires a location and at least one member", () => {
  assert.match(validateHousehold(null, [{ nickname: "Mom" }]).message, /location/);
  assert.match(validateHousehold(delhi, []).message, /at least one/);
});

test("validateHousehold checks nicknames and points at the bad member", () => {
  assert.deepEqual(validateHousehold(delhi, [{ nickname: "Mom" }, { nickname: "" }]).index, 1);
  assert.equal(validateHousehold(delhi, [{ nickname: "<b>" }]).index, 0);
  assert.match(validateHousehold(delhi, [{ nickname: "Mom" }, { nickname: "mom" }]).message, /different nicknames/);
  assert.equal(validateHousehold(delhi, [{ nickname: "आरव" }, { nickname: "Grandpa Joe" }]), null);
});

test("pick copies only the allowed keys", () => {
  assert.deepEqual(pick({ time: "t", us_aqi: 1, secret: 2 }, ["time", "us_aqi", "pm10"]), { time: "t", us_aqi: 1 });
  assert.deepEqual(pick(undefined, ["time"]), {});
});

test("errorMessage reads FastAPI error bodies", () => {
  assert.equal(errorMessage({ detail: "Too many requests." }), "Too many requests.");
  assert.equal(
    errorMessage({ detail: [{ loc: ["body"], msg: "Value error, Each household member needs a unique nickname." }] }),
    "Please check your details: Each household member needs a unique nickname.",
  );
  assert.match(errorMessage(null), /Something went wrong/);
});

test("member limit matches the server", () => {
  assert.equal(MAX_MEMBERS, 8);
});
