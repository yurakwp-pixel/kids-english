// 놀이판 배치(web/js/scene/layout.js): 같은 장면이면 늘 같은 자리. 순수 함수라 DOM 없이 시험한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { initialScene, applyOps } from "../../web/js/scene/model.js";
import { layout, footprint } from "../../web/js/scene/layout.js";

const CAT = JSON.parse(readFileSync(new URL("../../shared/catalog.json", import.meta.url), "utf8"));

const add = (kind, id, extra = {}) => ({ op: "add", kind, id, ...extra });
const build = (ops) => applyOps(initialScene(), ops, CAT);

// 화면에 차지하는 상자 (x, y 는 상자의 가운데)
function box(scene, pos, id) {
  const one = scene.items.find((item) => item.id === id);
  const kind = one ? one.kind : id;
  const fp = footprint(kind);
  const p = pos[id];
  return { left: p.x - (fp.w * p.scale) / 2, right: p.x + (fp.w * p.scale) / 2, top: p.y - (fp.h * p.scale) / 2, bottom: p.y + (fp.h * p.scale) / 2 };
}

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const inner of Object.values(value)) deepFreeze(inner);
  }
  return value;
}

test("layout is deterministic and 'on' is above ref", () => {
  const s = build([add("tree", "tree1"), add("ball", "ball1", { place: { rel: "on", ref: "tree1" } })]);
  const a = layout(s, CAT);
  const b = layout(s, CAT);
  assert.deepEqual(a, b);
  assert.ok(a.ball1.y < a.tree1.y);
});

test("layout gives every item and both actors a finite position and never touches the scene", () => {
  const s = deepFreeze(build([add("ball", "ball1"), add("socks", "socks1"), add("apple", "apple1", { place: { zone: "right" } })]));
  const pos = layout(s, CAT);
  assert.deepEqual(Object.keys(pos).sort(), ["apple1", "ball1", "chomp", "dino", "egg1", "nest1", "socks1"]);
  for (const [id, p] of Object.entries(pos)) {
    for (const key of ["x", "y", "scale"]) assert.ok(Number.isFinite(p[key]), `${id}.${key}`);
  }
  assert.deepEqual(layout(structuredClone(s), CAT), pos, "an equal scene gives an equal picture");
});

test("big is scale 1.4, small is 0.7, everything else is 1", () => {
  const s = build([add("ball", "ball1", { size: "big" }), add("ball", "ball2", { size: "small" }), add("ball", "ball3")]);
  const pos = layout(s, CAT);
  assert.equal(pos.ball1.scale, 1.4);
  assert.equal(pos.ball2.scale, 0.7);
  assert.equal(pos.ball3.scale, 1);
  assert.equal(pos.chomp.scale, 1);
  assert.equal(pos.dino.scale, 1);
});

test("'under' is below, 'next_to' is beside at the same ground level, 'in' is the same spot", () => {
  const s = build([
    add("tree", "tree1"),
    add("ball", "ball1", { place: { rel: "under", ref: "tree1" } }),
    add("block", "block1", { place: { rel: "next_to", ref: "tree1" } }),
  ]);
  const pos = layout(s, CAT);
  assert.ok(pos.ball1.y > pos.tree1.y, "under is lower on the screen");
  assert.ok(box(s, pos, "ball1").top >= box(s, pos, "tree1").bottom - 1, "and clear of it");
  const tree = box(s, pos, "tree1");
  const block = box(s, pos, "block1");
  assert.ok(block.right <= tree.left + 1 || block.left >= tree.right - 1, "beside, not overlapping");
  assert.ok(Math.abs(block.bottom - tree.bottom) < 1, "standing on the same ground");
  assert.deepEqual({ x: pos.egg1.x, y: pos.egg1.y }, { x: pos.nest1.x, y: pos.nest1.y });
});

test("'on' sits on top of the ref and stacks upward", () => {
  const s = build([
    add("block", "block1"),
    add("block", "block2", { place: { rel: "on", ref: "block1" } }),
    add("block", "block3", { place: { rel: "on", ref: "block2" } }),
    add("ball", "ball1", { place: { rel: "on", ref: "block3" } }),
  ]);
  const pos = layout(s, CAT);
  assert.ok(pos.block2.y < pos.block1.y && pos.block3.y < pos.block2.y && pos.ball1.y < pos.block3.y);
  assert.ok(Math.abs(box(s, pos, "block2").bottom - box(s, pos, "block1").top) < 1, "resting on it");
  assert.equal(pos.block2.x, pos.block1.x);
});

test("several things on, under, beside or in the same ref do not pile onto one spot", () => {
  const s = build([
    add("egg", "egg2", { place: { rel: "in", ref: "nest1" } }),
    add("egg", "egg3", { place: { rel: "in", ref: "nest1" } }),
    add("ball", "ball1", { place: { rel: "on", ref: "nest1" } }),
    add("ball", "ball2", { place: { rel: "on", ref: "nest1" } }),
    add("ball", "ball3", { place: { rel: "next_to", ref: "nest1" } }),
    add("ball", "ball4", { place: { rel: "next_to", ref: "nest1" } }),
    add("ball", "ball5", { place: { rel: "under", ref: "nest1" } }),
    add("ball", "ball6", { place: { rel: "under", ref: "nest1" } }),
  ]);
  const pos = layout(s, CAT);
  const spots = new Set(Object.entries(pos).filter(([id]) => id !== "egg1").map(([, p]) => `${p.x},${p.y}`));
  assert.equal(spots.size, Object.keys(pos).length - 1, "every id has its own spot (egg1 is the one in the nest's own spot)");
  assert.deepEqual({ x: pos.egg1.x, y: pos.egg1.y }, { x: pos.nest1.x, y: pos.nest1.y }, "the first one inside stays exactly in the nest");
});

test("things without a place fill the ground left to right without overlapping", () => {
  const s = build(Array.from({ length: 6 }, (_, i) => add("ball", `ball${i + 1}`)));
  const pos = layout(s, CAT);
  const nest = box(s, pos, "nest1");
  const first = box(s, pos, "ball1");
  assert.ok(first.left >= nest.right - 1 || first.bottom <= nest.top, "the first ball is not on the nest");
  for (let i = 1; i <= 6; i += 1) for (let j = i + 1; j <= 6; j += 1) {
    const a = box(s, pos, `ball${i}`);
    const b = box(s, pos, `ball${j}`);
    const apart = a.right <= b.left + 0.01 || b.right <= a.left + 0.01 || a.bottom <= b.top + 0.01 || b.bottom <= a.top + 0.01;
    assert.ok(apart || Math.abs(a.bottom - b.bottom) > 1, `ball${i} and ball${j} in the same row overlap`);
  }
});

test("zone slots: left, center and right are in order, sky is above the ground", () => {
  const s = build([
    add("ball", "ball1", { place: { zone: "right" } }),
    add("ball", "ball2", { place: { zone: "center" } }),
    add("ball", "ball3", { place: { zone: "left" } }),
    add("ball", "ball4", { place: { zone: "sky" } }),
    add("ball", "ball5", { place: { zone: "ground" } }),
  ]);
  const pos = layout(s, CAT);
  assert.ok(pos.ball3.x < pos.ball2.x && pos.ball2.x < pos.ball1.x);
  assert.ok(pos.ball3.x < 400 && pos.ball2.x > 400 && pos.ball2.x < 800 && pos.ball1.x > 800, "thirds of a 1200-wide view");
  assert.ok(pos.ball4.y < pos.ball5.y && pos.ball4.y < pos.dino.y, "the sky is above the ground and the actors");
});

test("many things in one zone get their own slots", () => {
  const ops = Array.from({ length: 9 }, (_, i) => add("ball", `ball${i + 1}`, { place: { zone: "center" } }));
  const s = build(ops);
  const pos = layout(s, CAT);
  const spots = new Set(ops.map((op) => `${pos[op.id].x},${pos[op.id].y}`));
  assert.equal(spots.size, 9);
  for (const op of ops) assert.ok(pos[op.id].x > 400 && pos[op.id].x < 800, `${op.id} stays in the center`);
});

test("a sun with no place goes to the sky", () => {
  const pos = layout(build([add("sun", "sun1")]), CAT);
  assert.ok(pos.sun1.y < pos.dino.y - 60);
});

test("a full scene of 20 things: every one has its own spot inside the view", () => {
  const kinds = ["ball", "block", "apple", "tree", "socks", "hat", "cookie", "banana", "flower", "toys"];
  const ops = Array.from({ length: 30 }, (_, i) => add(kinds[i % kinds.length], `${kinds[i % kinds.length]}${Math.floor(i / kinds.length) + 1}`, i % 4 === 0 ? { size: "big" } : {}));
  const s = build(ops);
  assert.equal(s.items.length, 20);
  const pos = layout(s, CAT);
  const view = { w: 1200, h: 600 };
  const spots = new Set();
  for (const one of s.items) {
    const b = box(s, pos, one.id);
    assert.ok(b.left >= -0.01 && b.right <= view.w + 0.01 && b.top >= -0.01 && b.bottom <= view.h + 0.01, `${one.id} is inside the view`);
    spots.add(`${pos[one.id].x},${pos[one.id].y}`);
  }
  assert.equal(spots.size, 20);
});

test("actors have fixed homes that do not depend on the items", () => {
  const empty = layout(initialScene(), CAT);
  const busy = layout(build([add("tree", "tree1"), add("ball", "ball1"), add("sun", "sun1"), { op: "act", actor: "dino", action: "dance" }]), CAT);
  assert.deepEqual(busy.chomp, empty.chomp);
  assert.deepEqual(busy.dino, empty.dino);
  assert.ok(empty.dino.x < empty.chomp.x, "the dino stands to the left of Chomp");
  for (const id of ["chomp", "dino"]) {
    const fp = footprint(id);
    assert.ok(empty[id].x - fp.w / 2 >= 0 && empty[id].x + fp.w / 2 <= 1200, id);
    assert.ok(empty[id].y - fp.h / 2 >= 0 && empty[id].y + fp.h / 2 <= 600, id);
  }
});

test("tall things keep out of the actors' way", () => {
  const s = build(Array.from({ length: 5 }, (_, i) => add("tree", `tree${i + 1}`, { size: i % 2 ? "big" : "small" })));
  const pos = layout(s, CAT);
  for (const actor of ["chomp", "dino"]) {
    const a = box(s, pos, actor);
    for (const one of s.items.filter((item) => item.kind === "tree")) {
      const t = box(s, pos, one.id);
      const crossX = t.right > a.left + 0.01 && t.left < a.right - 0.01;
      const crossY = t.bottom > a.top + 0.01 && t.top < a.bottom - 0.01;
      assert.ok(!(crossX && crossY), `${one.id} stands in front of ${actor}`);
    }
  }
});

test("a worn hat sits on the dino's head and worn socks at its feet", () => {
  const s = build([
    add("hat", "hat1"), add("socks", "socks1"), add("socks", "socks2"),
    { op: "act", actor: "dino", action: "put_on", target: "hat1" },
    { op: "act", actor: "dino", action: "put_on", target: "socks1" },
    { op: "act", actor: "dino", action: "put_on", target: "socks2" },
  ]);
  const pos = layout(s, CAT);
  const dino = box(s, pos, "dino");
  assert.ok(pos.hat1.y < pos.dino.y, "hat above the middle");
  assert.ok(Math.abs(pos.hat1.y - dino.top) < footprint("hat").h, "at the top of the head");
  assert.ok(Math.abs(pos.hat1.x - pos.dino.x) < 30);
  for (const id of ["socks1", "socks2"]) {
    assert.ok(pos[id].y > pos.dino.y, `${id} down at the feet`);
    assert.ok(Math.abs(box(s, pos, id).bottom - dino.bottom) < 1);
    assert.ok(Math.abs(pos[id].x - pos.dino.x) < footprint("dino").w / 2);
  }
  assert.notEqual(pos.socks1.x, pos.socks2.x, "a pair, not one pile");
});

test("a place that points nowhere or loops still gets a spot", () => {
  const s = build([add("ball", "ball1"), add("block", "block1")]);
  s.items.find((one) => one.id === "ball1").place = { rel: "on", ref: "ghost" };
  s.items.find((one) => one.id === "block1").place = { rel: "next_to", ref: "block1" };
  s.items.push({ id: "ball2", kind: "ball", place: { rel: "on", ref: "ball3" } }, { id: "ball3", kind: "ball", place: { rel: "under", ref: "ball2" } });
  s.items.push({ id: "odd1", kind: "dragon" }, { id: "odd2", kind: "ball", size: "huge", place: { zone: "moon" } });
  const pos = layout(s, CAT);
  for (const one of s.items) {
    assert.ok(Number.isFinite(pos[one.id].x) && Number.isFinite(pos[one.id].y) && Number.isFinite(pos[one.id].scale), one.id);
  }
  assert.equal(pos.odd2.scale, 1, "an unknown size counts as normal");
});

test("a different view size changes the picture but stays finite and inside", () => {
  const s = build([add("tree", "tree1", { size: "big" }), add("ball", "ball1", { place: { rel: "on", ref: "tree1" } }), add("sun", "sun1", { size: "big" })]);
  const wide = layout(s, CAT, { w: 1200, h: 600 });
  assert.deepEqual(layout(s, CAT, { w: 1200, h: 600 }), layout(s, CAT));
  assert.deepEqual(layout(s, CAT, {}), wide, "missing fields fall back to 1200x600");
  const small = layout(s, CAT, { w: 800, h: 450 });
  assert.notDeepEqual(small, wide);
  assert.ok(small.dino.x < 800 && small.chomp.x < 800 && small.dino.y < 450);
  for (const p of Object.values(small)) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  assert.ok(small.ball1.y < small.tree1.y);
});

test("fuzz: any scene the model can reach lays out inside the view with a spot per id", () => {
  let seed = 12345;
  const next = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
  const kinds = Object.keys(CAT.objects);
  const rels = CAT.relations;
  for (let round = 0; round < 60; round += 1) {
    let s = initialScene();
    for (let step = 0; step < 12; step += 1) {
      const ids = s.items.map((one) => one.id);
      const pick = () => (ids.length ? ids[next(ids.length)] : "ghost");
      const kind = kinds[next(kinds.length)];
      const place = next(3) === 0 ? { zone: CAT.zones[next(CAT.zones.length)] } : next(3) === 0 ? { rel: rels[next(rels.length)], ref: pick() } : undefined;
      const op = [
        { op: "add", kind, count: 1 + next(5), ...(next(2) ? { size: CAT.sizes[next(2)] } : {}), ...(place ? { place } : {}) },
        { op: "move", target: pick(), place: place ?? { zone: "left" } },
        { op: "remove", target: pick() },
        { op: "act", actor: "dino", action: next(2) ? "put_on" : "hatch", target: pick() },
      ][next(4)];
      s = applyOps(s, [op], CAT);
    }
    const pos = layout(s, CAT);
    assert.deepEqual(Object.keys(pos).sort(), [...s.items.map((one) => one.id), "chomp", "dino"].sort());
    for (const one of s.items) {
      const b = box(s, pos, one.id);
      assert.ok(b.left >= -0.01 && b.right <= 1200.01 && b.top >= -0.01 && b.bottom <= 600.01, `${one.id} outside the view in round ${round}`);
    }
  }
});
