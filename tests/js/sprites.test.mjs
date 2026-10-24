import { test } from "node:test";
import assert from "node:assert/strict";
import { chompSvg, CHOMP_FACES } from "../../web/js/scene/sprites.js";

test("chompSvg draws a 200x200 SVG with the face and mouth state as data attributes", () => {
  const svg = chompSvg({ mouthOpen: true, face: "happy" });
  assert.match(svg, /^<svg [^>]*viewBox="0 0 200 200"/);
  assert.match(svg, /data-face="happy"/);
  assert.match(svg, /data-mouth="open"/);
  assert.match(svg, /<\/svg>$/);
});

test("the two mouth states draw different mouths", () => {
  const open = chompSvg({ mouthOpen: true, face: "happy" });
  const closed = chompSvg({ mouthOpen: false, face: "happy" });
  assert.notEqual(open, closed);
  assert.match(closed, /data-mouth="closed"/);
});

test("every catalog face draws its own eyes; unknown faces fall back to happy", () => {
  const drawn = new Set(CHOMP_FACES.map((face) => chompSvg({ mouthOpen: true, face })));
  assert.equal(drawn.size, CHOMP_FACES.length);
  for (const face of ["happy", "excited", "sleepy", "surprised", "sad", "shy"]) assert.ok(CHOMP_FACES.includes(face), face);
  assert.equal(chompSvg({ mouthOpen: true, face: "<script>" }), chompSvg({ mouthOpen: true, face: "happy" }));
  assert.equal(chompSvg(), chompSvg({ mouthOpen: true, face: "happy" }));
});

test("chompSvg loads nothing from outside", () => {
  for (const face of CHOMP_FACES) {
    const svg = chompSvg({ mouthOpen: false, face }).replace('xmlns="http://www.w3.org/2000/svg"', "");
    assert.doesNotMatch(svg, /https?:|href|url\(/);
  }
});
