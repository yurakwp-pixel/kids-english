// 한 장면을 그리기 위한 순수 계산(web/js/scene/frame.js): 그리는 순서, 앞뒤 장면의 차이, 포즈의 CSS transform.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { drawOrder, frontKey, diffScene, partTransform, poseTransforms } from "../../web/js/scene/frame.js";
import { initialScene, applyOps } from "../../web/js/scene/model.js";
import { layout } from "../../web/js/scene/layout.js";
import { PARTS, POSES } from "../../web/js/scene/poses.js";

const CAT = JSON.parse(readFileSync(new URL("../../shared/catalog.json", import.meta.url), "utf8"));
const HATCH = { op: "act", actor: "dino", action: "hatch" };
const order = (scene) => drawOrder(scene, layout(scene, CAT));
const before = (list, a, b) => {
  assert.ok(list.includes(a), `${a} missing`);
  assert.ok(list.includes(b), `${b} missing`);
  return list.indexOf(a) < list.indexOf(b);
};

// ---------------------------------------------------------------- 그리는 순서

test("first screen: the egg is drawn after its nest, and the nest's front rim after the egg", () => {
  const list = order(initialScene());
  assert.ok(before(list, "nest1", "egg1"));
  assert.ok(before(list, "egg1", frontKey("nest1")));
  assert.equal(frontKey("nest1"), "nest1#front");
  assert.ok(list.includes("chomp") && list.includes("dino")); // 아직 안 깬 공룡도 자리는 있다(렌더러가 숨긴다)
});

test("an item in another is drawn right after its ref even when its bottom edge is higher", () => {
  let s = applyOps(initialScene(), [{ op: "add", kind: "ball", id: "ball1", size: "small", place: { rel: "in", ref: "nest1" } }], CAT);
  const pos = layout(s, CAT);
  // 작은 공은 바닥끝이 둥지보다 위지만, 둥지 다음에 그린다
  assert.ok(pos.ball1.y + 32 * 0.7 < pos.nest1.y + 32);
  const list = drawOrder(s, pos);
  assert.ok(before(list, "nest1", "ball1") && before(list, "ball1", "nest1#front"));
  s = applyOps(s, [{ op: "add", kind: "egg", id: "egg2", place: { rel: "in", ref: "nest1" } }], CAT);
  const two = order(s);
  assert.ok(before(two, "egg1", "ball1") && before(two, "ball1", "egg2") && before(two, "egg2", "nest1#front"));
});

test("everything else is ordered by bottom edge: lower on screen is drawn later (in front)", () => {
  const s = applyOps(initialScene(), [HATCH,
    { op: "add", kind: "ball", ids: ["ball1", "ball2", "ball3", "ball4", "ball5"], count: 5 },
    { op: "add", kind: "tree", id: "tree1" }], CAT);
  const pos = layout(s, CAT);
  const list = drawOrder(s, pos);
  const bottom = (id) => pos[id].y + ({ chomp: 150, dino: 150, ball: 64, tree: 230, nest: 64, egg: 72 }[s.items.find((i) => i.id === id)?.kind ?? id] * pos[id].scale) / 2;
  const roots = list.filter((key) => !key.includes("#") && key !== "egg1");
  for (let i = 1; i < roots.length; i += 1) assert.ok(bottom(roots[i - 1]) <= bottom(roots[i]) + 1e-9, `${roots[i - 1]} then ${roots[i]}`);
  assert.ok(before(list, "dino", "ball1")); // 앞마당 물건은 캐릭터보다 앞
});

test("ties keep scene order (actors first, then items as they were added)", () => {
  const s = applyOps(initialScene(), [{ op: "add", kind: "ball", ids: ["ball1", "ball2"], count: 2 }], CAT);
  const pos = layout(s, CAT);
  pos.ball2 = { ...pos.ball2, y: pos.ball1.y };
  const list = drawOrder(s, pos);
  assert.ok(before(list, "ball1", "ball2"));
  assert.ok(before(list, "chomp", "dino") || before(list, "dino", "chomp"));
});

test("worn things follow their wearer; every key appears exactly once", () => {
  const s = applyOps(initialScene(), [HATCH, { op: "add", kind: "socks", id: "socks1" }, { op: "add", kind: "hat", id: "hat1" },
    { op: "act", actor: "dino", action: "put_on", target: "socks1" }, { op: "act", actor: "dino", action: "put_on", target: "hat1" }], CAT);
  const list = order(s);
  const at = list.indexOf("dino");
  assert.deepEqual(list.slice(at + 1, at + 3).sort(), ["hat1", "socks1"]);
  assert.equal(new Set(list).size, list.length);
});

test("an item whose position is unknown is still drawn, after the known ones", () => {
  const s = applyOps(initialScene(), [{ op: "add", kind: "ball", id: "ball1" }], CAT);
  const pos = layout(s, CAT);
  delete pos.ball1;
  const list = drawOrder(s, pos);
  assert.equal(list.at(-1), "ball1");
});

test("a broken in-loop does not hang and draws both items", () => {
  const s = initialScene();
  s.items.push({ id: "a1", kind: "ball", place: { rel: "in", ref: "b1" } }, { id: "b1", kind: "ball", place: { rel: "in", ref: "a1" } });
  const list = drawOrder(s, layout(s, CAT));
  assert.ok(list.includes("a1") && list.includes("b1"));
  assert.equal(new Set(list).size, list.length);
});

// ---------------------------------------------------------------- 앞뒤 장면의 차이

test("diffScene on the first render reports everything as added and nothing to animate", () => {
  const d = diffScene(null, initialScene(), []);
  assert.deepEqual(d.added, ["nest1", "egg1"]);
  assert.deepEqual(d.removed, []);
  assert.equal(d.hatched, false);
  assert.deepEqual(d.acts, []);
});

test("hatching removes the first egg as 'hatched' and flags the hatch", () => {
  const a = initialScene();
  const b = applyOps(a, [HATCH], CAT);
  const d = diffScene(a, b, [HATCH]);
  assert.equal(d.hatched, true);
  assert.deepEqual(d.removed, [{ id: "egg1", kind: "egg", how: "hatched" }]);
  assert.deepEqual(d.acts, [{ actor: "dino", action: "hatch", target: null }]);
});

test("food Chomp chomps leaves as 'eaten'; other removals leave as 'gone'", () => {
  const a = applyOps(initialScene(), [{ op: "add", kind: "apple", id: "apple1" }, { op: "add", kind: "ball", id: "ball1" }], CAT);
  const ops = [{ op: "act", actor: "chomp", action: "chomp", target: "apple1" }, { op: "remove", target: "ball1" }];
  const d = diffScene(a, applyOps(a, ops, CAT), ops);
  assert.deepEqual(d.removed, [{ id: "apple1", kind: "apple", how: "eaten" }, { id: "ball1", kind: "ball", how: "gone" }]);
  assert.deepEqual(d.acts, [{ actor: "chomp", action: "chomp", target: "apple1" }]);
});

test("acts come from the ops so the same action twice plays twice; idle resets are not acts", () => {
  const a = applyOps(initialScene(), [HATCH, { op: "act", actor: "dino", action: "kick" }], CAT);
  const again = [{ op: "act", actor: "dino", action: "kick", target: "ball1" }];
  assert.deepEqual(diffScene(a, applyOps(a, again, CAT), again).acts, [{ actor: "dino", action: "kick", target: "ball1" }]);
  const rest = [{ op: "act", actor: "dino", action: "idle" }];
  assert.deepEqual(diffScene(a, applyOps(a, rest, CAT), rest).acts, []);
  // ops 를 모르면 상태가 바뀐 것만 동작으로 본다
  const b = applyOps(a, [{ op: "act", actor: "chomp", action: "jump" }], CAT);
  assert.deepEqual(diffScene(a, b).acts, [{ actor: "chomp", action: "jump", target: null }]);
});

test("added items and changed looks (set color) are reported", () => {
  const a = applyOps(initialScene(), [{ op: "add", kind: "ball", id: "ball1" }], CAT);
  const ops = [{ op: "set", target: "ball1", color: "blue" }, { op: "add", kind: "egg", id: "egg2", color: "red", size: "big" }];
  const d = diffScene(a, applyOps(a, ops, CAT), ops);
  assert.deepEqual(d.added, ["egg2"]);
  assert.deepEqual(d.changed, ["ball1"]);
});

// ---------------------------------------------------------------- 포즈

test("partTransform writes rotate then translate, always in the same shape so CSS can tween it", () => {
  assert.equal(partTransform({ rot: -60 }), "rotate(-60deg) translate(0px, 0px)");
  assert.equal(partTransform({ dx: 6, dy: -6, rot: 75 }), "rotate(75deg) translate(6px, -6px)");
  assert.equal(partTransform(undefined), "rotate(0deg) translate(0px, 0px)");
  assert.equal(partTransform({ rot: "9;}" }), "rotate(0deg) translate(0px, 0px)"); // 숫자만
});

test("poseTransforms gives every part a transform; parts the pose does not move are at rest", () => {
  const kick = poseTransforms("kick");
  assert.deepEqual(Object.keys(kick).sort(), [...PARTS].sort());
  assert.equal(kick.legR, partTransform(POSES.kick.legR));
  assert.equal(kick.head, "rotate(0deg) translate(0px, 0px)");
  for (const value of Object.values(poseTransforms("no-such-action"))) assert.equal(value, "rotate(0deg) translate(0px, 0px)");
});
