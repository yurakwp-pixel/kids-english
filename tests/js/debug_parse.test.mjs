import { test } from "node:test";
import assert from "node:assert/strict";
import { parseNbest } from "../../web/js/debug.js";

test("parseNbest turns comma-separated text into a ranked n-best list", () => {
  assert.deepEqual(parseNbest(" hello chomp, hello champ ,,"), [{ t: "hello chomp", c: 1 }, { t: "hello champ", c: 0.9 }]);
});

test("parseNbest keeps at most 5 and gives [] for empty input", () => {
  assert.equal(parseNbest("a,b,c,d,e,f,g").length, 5);
  assert.deepEqual(parseNbest("   "), []);
});
