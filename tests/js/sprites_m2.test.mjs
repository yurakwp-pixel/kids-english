// 아기 공룡과 물건 그림(web/js/scene/sprites.js, Task 15). 문자열만 만드는 순수 함수라 node 로 시험한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  dinoSvg, dinoFaceSvg, dinoWornSvg, DINO_FACES, DINO_PIVOTS, DINO_SLOTS,
  objectSvg, SVG_OBJECTS, PALETTE,
} from "../../web/js/scene/sprites.js";
import { PARTS } from "../../web/js/scene/poses.js";
import { footprint } from "../../web/js/scene/layout.js";

const CAT = JSON.parse(readFileSync(new URL("../../shared/catalog.json", import.meta.url), "utf8"));
const outsideFree = (svg) => svg.replaceAll('xmlns="http://www.w3.org/2000/svg"', "");

test("dinoSvg draws one group per pose part, every part pivoting on its own joint", () => {
  const svg = dinoSvg();
  assert.match(svg, /^<svg [^>]*viewBox="0 0 120 150"/);
  assert.match(svg, /<\/svg>$/);
  for (const part of PARTS) {
    const groups = svg.match(new RegExp(`<g data-part="${part}"`, "g")) ?? [];
    assert.equal(groups.length, 1, part);
    const { x, y } = DINO_PIVOTS[part];
    assert.match(svg, new RegExp(`<g data-part="${part}" style="transform-origin:${x}px ${y}px"`), part);
  }
  assert.deepEqual(Object.keys(DINO_PIVOTS).sort(), [...PARTS].sort());
});

test("the body group holds every other part, so a body pose moves the whole dino", () => {
  const svg = dinoSvg();
  const open = svg.indexOf('<g data-part="body"');
  assert.ok(open > 0);
  // body 가 맨 바깥 부위다: 다른 부위는 모두 body 가 열린 뒤에 나온다
  for (const part of PARTS.filter((p) => p !== "body")) assert.ok(svg.indexOf(`data-part="${part}"`) > open, part);
  // 깊이를 세어 body 가 닫히는 곳을 찾는다: 그 뒤에는 부위가 없다
  let depth = 0;
  let close = -1;
  const re = /<g\b|<\/g>/g;
  re.lastIndex = open;
  for (let m = re.exec(svg); m; m = re.exec(svg)) {
    depth += m[0] === "</g>" ? -1 : 1;
    if (depth === 0) { close = m.index; break; }
  }
  assert.ok(close > open);
  assert.equal(svg.slice(close).includes("data-part="), false);
});

test("the baby dino is green and cute: big eye, blush, no teeth or claws", () => {
  const svg = dinoSvg();
  assert.match(svg, /fill="#6cc56b"/i); // 초록 몸
  assert.match(svg, /data-slot="face"/);
  assert.doesNotMatch(svg, /teeth|claw|fang/i);
});

test("dinoSvg has empty slots for the face, the hat and a sock on each foot", () => {
  const svg = dinoSvg();
  for (const slot of DINO_SLOTS) assert.match(svg, new RegExp(`data-slot="${slot}"`), slot);
  assert.deepEqual([...DINO_SLOTS].sort(), ["face", "hat", "sockL", "sockR"]);
  // 양말 자리는 그 다리 안에 있어야 다리와 함께 움직인다
  const legL = svg.indexOf('data-part="legL"');
  const legR = svg.indexOf('data-part="legR"');
  assert.ok(svg.indexOf('data-slot="sockL"') > legL && svg.indexOf('data-slot="sockL"') < legR);
  assert.ok(svg.indexOf('data-slot="hat"') > svg.indexOf('data-part="head"'));
});

test("every catalog dino face draws its own face; unknown faces fall back to happy", () => {
  for (const face of CAT.actors.dino.faces) assert.ok(DINO_FACES.includes(face), face);
  const drawn = new Set(DINO_FACES.map((face) => dinoFaceSvg(face)));
  assert.equal(drawn.size, DINO_FACES.length);
  assert.equal(dinoFaceSvg("<script>"), dinoFaceSvg("happy"));
  assert.equal(dinoFaceSvg(), dinoFaceSvg("happy"));
  assert.ok(dinoSvg().includes(dinoFaceSvg("happy")));
});

test("dinoWornSvg draws socks on the named foot and a hat on the head, in palette colors", () => {
  const red = dinoWornSvg("sockL", "red");
  assert.match(red, new RegExp(PALETTE.red.fill, "i"));
  assert.notEqual(dinoWornSvg("sockL", "red"), dinoWornSvg("sockR", "red"));
  assert.match(dinoWornSvg("hat", "blue"), new RegExp(PALETTE.blue.fill, "i"));
  assert.equal(dinoWornSvg("hat", "url(#x)"), dinoWornSvg("hat", undefined)); // 모르는 색은 기본색
  assert.equal(dinoWornSvg("nose", "red"), "");
});

test("objectSvg draws every svg object of the catalog in its footprint box", () => {
  const svgKinds = Object.entries(CAT.objects).filter(([, def]) => def.render === "svg").map(([kind]) => kind);
  assert.deepEqual([...SVG_OBJECTS].sort(), svgKinds.sort());
  for (const kind of svgKinds) {
    const svg = objectSvg(kind, {});
    const { w, h } = footprint(kind);
    assert.match(svg, new RegExp(`^<svg [^>]*viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"`), kind);
    assert.match(svg, new RegExp(`data-kind="${kind}"`), kind);
    assert.match(svg, /<\/svg>$/, kind);
  }
});

test("color comes only from the palette: a red egg is red, an unknown color is the default", () => {
  assert.equal(Object.keys(PALETTE).sort().join(), [...CAT.colors].sort().join());
  for (const kind of SVG_OBJECTS) {
    for (const color of CAT.colors) assert.match(objectSvg(kind, { color }), new RegExp(PALETTE[color].fill, "i"), `${kind} ${color}`);
    assert.equal(objectSvg(kind, { color: '"/><script>' }), objectSvg(kind, {}), kind);
  }
  assert.notEqual(objectSvg("egg", { color: "red" }), objectSvg("egg", {}));
});

test("size is recorded but not drawn twice (layout scales the box)", () => {
  const big = objectSvg("egg", { color: "red", size: "big" });
  assert.match(big, /data-size="big"/);
  assert.match(big, /viewBox="0 0 56 72" width="56" height="72"/);
  assert.doesNotMatch(objectSvg("egg", { size: "huge" }), /data-size/);
});

test("the egg carries a hidden crack the hatch can show", () => {
  assert.match(objectSvg("egg", {}), /<g data-crack="" opacity="0"/);
});

test("the nest has a front rim layer so an egg can sit inside it", () => {
  const front = objectSvg("nest", { part: "front" });
  assert.match(front, /data-part-of="front"/);
  assert.notEqual(front, objectSvg("nest", {}));
  assert.match(objectSvg("nest", { color: "pink", part: "front" }), new RegExp(PALETTE.pink.fill, "i"));
});

test("emoji objects are drawn as <text> with the catalog emoji, escaped", () => {
  const apple = objectSvg("apple", { emoji: CAT.objects.apple.emoji });
  assert.match(apple, /<text [^>]*>🍎<\/text>/);
  assert.match(apple, /viewBox="0 0 56 56"/);
  const evil = objectSvg("flower", { emoji: '<img src=x onerror=alert(1)>' });
  assert.doesNotMatch(evil, /<img/);
  assert.match(evil, /&lt;img/);
});

test("an unknown kind without an emoji still draws something harmless", () => {
  const svg = objectSvg("<script>", {});
  assert.match(svg, /^<svg /);
  assert.doesNotMatch(svg, /<script/);
});

test("dino and object sprites load nothing from outside", () => {
  const all = [dinoSvg(), ...DINO_FACES.map((f) => dinoFaceSvg(f)), ...DINO_SLOTS.map((s) => dinoWornSvg(s, "red")),
    ...SVG_OBJECTS.flatMap((k) => [objectSvg(k, {}), objectSvg(k, { color: "blue", part: "front" })]),
    objectSvg("apple", { emoji: "🍎" })];
  for (const svg of all) assert.doesNotMatch(outsideFree(svg), /https?:|href|url\(|<image|<foreignObject/i);
});
