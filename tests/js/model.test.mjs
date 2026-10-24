// 장면 모델(web/js/scene/model.js): 서버 ops 를 화면 상태에 적용하는 순수 함수를 시험한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { initialScene, applyOps, toWire } from "../../web/js/scene/model.js";

const CAT = JSON.parse(readFileSync(new URL("../../shared/catalog.json", import.meta.url), "utf8"));

const add = (kind, id, extra = {}) => ({ op: "add", kind, id, ...extra });
const ids = (scene) => scene.items.map((item) => item.id);
const item = (scene, id) => scene.items.find((one) => one.id === id);

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const inner of Object.values(value)) deepFreeze(inner);
  }
  return value;
}

test("initialScene has nest1 on the left, egg1 in it, idle actors and an unhatched dino", () => {
  const s = initialScene();
  assert.deepEqual(s.items, [
    { id: "nest1", kind: "nest", place: { zone: "left" } },
    { id: "egg1", kind: "egg", place: { rel: "in", ref: "nest1" } },
  ]);
  assert.deepEqual(s.actors.chomp, { state: "idle", face: "happy", wearing: [] });
  assert.deepEqual(s.actors.dino, { state: "idle", face: "happy", hatched: false, wearing: [] });
  assert.equal(typeof s.counters, "object");
});

test("initialScene returns a fresh object every time", () => {
  const a = initialScene();
  a.items.push({ id: "x", kind: "ball" });
  a.actors.dino.wearing.push("x");
  assert.equal(initialScene().items.length, 2);
  assert.deepEqual(initialScene().actors.dino.wearing, []);
});

test("applyOps is pure and adds with server ids", () => {
  const s0 = initialScene();
  const s1 = applyOps(s0, [{ op: "add", kind: "egg", id: "egg2", color: "red", size: "big" }], CAT);
  assert.equal(s0.items.length, 2);
  assert.deepEqual(s1.items.at(-1), { id: "egg2", kind: "egg", color: "red", size: "big" });
});

test("applyOps never mutates a frozen scene or frozen ops", () => {
  const scene = deepFreeze(initialScene());
  const ops = deepFreeze([
    add("tree", "tree1", { size: "big", place: { zone: "right" } }),
    add("socks", "socks1"),
    { op: "act", actor: "dino", action: "put_on", target: "socks1" },
    { op: "set", target: "tree1", color: "green" },
    { op: "set", target: "chomp", face: "excited" },
    { op: "move", target: "tree1", place: { zone: "center" } },
    { op: "act", actor: "dino", action: "hatch" },
    { op: "remove", target: "socks1" },
  ]);
  const next = applyOps(scene, ops, CAT);
  assert.notEqual(next, scene);
  assert.deepEqual(ids(scene), ["nest1", "egg1"]);
  assert.equal(next.actors.chomp.face, "excited");
});

test("a result shares no objects with the ops it came from", () => {
  const place = { rel: "next_to", ref: "nest1" };
  const s = applyOps(initialScene(), [add("ball", "ball1", { place })], CAT);
  place.ref = "elsewhere";
  assert.deepEqual(item(s, "ball1").place, { rel: "next_to", ref: "nest1" });
});

test("add with ids and count makes several items that keep the server's ids", () => {
  const s = applyOps(initialScene(), [{ op: "add", kind: "ball", ids: ["ball3", "ball4", "ball5"], count: 3, color: "blue" }], CAT);
  assert.deepEqual(ids(s).slice(2), ["ball3", "ball4", "ball5"]);
  for (const id of ["ball3", "ball4", "ball5"]) assert.deepEqual(item(s, id), { id, kind: "ball", color: "blue" });
});

test("add keeps a valid place (zone or relation) and drops an invalid one", () => {
  const s = applyOps(initialScene(), [
    add("ball", "ball1", { place: { zone: "sky" } }),
    add("ball", "ball2", { place: { rel: "on", ref: "nest1" } }),
    add("ball", "ball3", { place: { rel: "on", ref: "nobody" } }),
    add("ball", "ball4", { place: { zone: "moon" } }),
    add("ball", "ball5", { place: { rel: "beside", ref: "nest1" } }),
    add("ball", "ball6", { place: { rel: "on", ref: "chomp" } }),
  ], CAT);
  assert.deepEqual(item(s, "ball1").place, { zone: "sky" });
  assert.deepEqual(item(s, "ball2").place, { rel: "on", ref: "nest1" });
  for (const id of ["ball3", "ball4", "ball5", "ball6"]) {
    assert.ok(item(s, id), id);
    assert.equal("place" in item(s, id), false, id);
  }
});

test("add ignores unknown kinds and ids that are unusable", () => {
  const before = initialScene();
  const s = applyOps(before, [
    add("dragon", "dragon1"),
    add("ball", "nest1"),
    add("ball", "chomp"),
    add("ball", "dino"),
    add("ball", ""),
    add("ball", "<script>"),
  ], CAT);
  assert.deepEqual(s.items, before.items);
});

test("add keeps only colors, sizes and attributes the catalog allows for that kind", () => {
  const s = applyOps(initialScene(), [
    add("ball", "ball1", { color: "magenta", size: "huge" }),
    add("apple", "apple1", { color: "red", size: "big" }),
    add("socks", "socks1", { color: "pink", size: "big" }),
  ], CAT);
  assert.deepEqual(item(s, "ball1"), { id: "ball1", kind: "ball" });
  assert.deepEqual(item(s, "apple1"), { id: "apple1", kind: "apple" });
  assert.deepEqual(item(s, "socks1"), { id: "socks1", kind: "socks", color: "pink" });
});

test("add skips an id that is already in the scene", () => {
  const s = applyOps(initialScene(), [add("egg", "egg1", { color: "red" }), add("ball", "ball1"), add("ball", "ball1", { color: "red" })], CAT);
  assert.deepEqual(item(s, "egg1"), { id: "egg1", kind: "egg", place: { rel: "in", ref: "nest1" } });
  assert.equal(s.items.filter((one) => one.id === "ball1").length, 1);
  assert.equal(item(s, "ball1").color, undefined);
});

test("without id or ids, new ids follow the server rule: kind plus max existing number plus one", () => {
  let s = applyOps(initialScene(), [{ op: "add", kind: "egg" }], CAT);
  assert.deepEqual(ids(s), ["nest1", "egg1", "egg2"]);
  s = applyOps(s, [{ op: "add", kind: "egg", count: 3, color: "red" }], CAT);
  assert.deepEqual(ids(s).slice(3), ["egg3", "egg4", "egg5"]);
  assert.equal(item(s, "egg4").color, "red");
  s = applyOps(s, [{ op: "add", kind: "ball" }], CAT);
  assert.equal(ids(s).at(-1), "ball1");
});

test("id numbers continue past the highest seen number and gaps are not filled", () => {
  let s = applyOps(initialScene(), [add("ball", "ball1"), add("ball", "ball7"), { op: "add", kind: "ball" }], CAT);
  assert.equal(ids(s).at(-1), "ball8");
  s = applyOps(s, [{ op: "remove", target: "ball8" }, { op: "add", kind: "ball" }], CAT);
  assert.equal(ids(s).at(-1), "ball9", "an id removed earlier is not handed out again");
});

test("defensive count is capped at max_count and defaults to one", () => {
  const s = applyOps(initialScene(), [{ op: "add", kind: "ball", count: 99 }, { op: "add", kind: "block", count: "many" }], CAT);
  assert.equal(s.items.filter((one) => one.kind === "ball").length, CAT.max_count);
  assert.equal(s.items.filter((one) => one.kind === "block").length, 1);
});

test("act sets the actor state; unknown actors, actions and ops are ignored", () => {
  const s0 = initialScene();
  const s = applyOps(s0, [{ op: "act", actor: "dino", action: "kick", target: "ball1" }, { op: "act", actor: "chomp", action: "bark" }], CAT);
  assert.equal(s.actors.dino.state, "kick");
  assert.equal(s.actors.chomp.state, "bark");
  const same = applyOps(s0, [
    { op: "act", actor: "robot", action: "kick" },
    { op: "act", actor: "dino", action: "fly" },
    { op: "act", actor: "chomp", action: "kick" },
    { op: "act", actor: "__proto__", action: "kick" },
    { op: "act", action: "kick" },
  ], CAT);
  assert.deepEqual(same, s0);
});

test("act idle puts an actor back to rest (a local reset the server never sends)", () => {
  const s = applyOps(initialScene(), [{ op: "act", actor: "dino", action: "dance" }, { op: "act", actor: "dino", action: "idle" }], CAT);
  assert.equal(s.actors.dino.state, "idle");
});

test("hatch removes egg1 and hatches the dino", () => {
  const s = applyOps(initialScene(), [{ op: "act", actor: "dino", action: "hatch" }], CAT);
  assert.equal(s.actors.dino.hatched, true);
  assert.equal(s.actors.dino.state, "hatch");
  assert.ok(!s.items.some((one) => one.id === "egg1"));
  assert.ok(s.items.some((one) => one.id === "nest1"));
});

test("hatch is idempotent: a re-sent hatch never deletes a later egg1 the child made", () => {
  let s = applyOps(initialScene(), [{ op: "act", actor: "dino", action: "hatch" }], CAT);
  s = applyOps(s, [add("egg", "egg1", { color: "red" })], CAT); // the server hands out egg1 again once no egg is left
  s = applyOps(s, [{ op: "act", actor: "dino", action: "walk" }], CAT);
  const again = applyOps(s, [{ op: "act", actor: "dino", action: "hatch" }], CAT);
  assert.deepEqual(item(again, "egg1"), { id: "egg1", kind: "egg", color: "red" });
  assert.equal(again.actors.dino.hatched, true);
  assert.equal(again.actors.dino.state, "walk", "a repeated hatch does not replay the hatch pose");
  assert.deepEqual(again.items, s.items);
});

test("hatch in the same batch as other ops works once", () => {
  const s = applyOps(initialScene(), [
    { op: "act", actor: "dino", action: "hatch" },
    add("ball", "ball1"),
    { op: "act", actor: "dino", action: "kick", target: "ball1" },
    { op: "act", actor: "dino", action: "hatch" },
  ], CAT);
  assert.equal(s.actors.dino.state, "kick");
  assert.deepEqual(ids(s), ["nest1", "ball1"]);
});

test("put_on marks a wearable target as worn by the actor and lists it in wearing", () => {
  const s = applyOps(initialScene(), [
    add("socks", "socks1", { color: "red", place: { zone: "center" } }),
    { op: "act", actor: "dino", action: "put_on", target: "socks1" },
  ], CAT);
  assert.deepEqual(s.actors.dino.wearing, ["socks1"]);
  assert.equal(s.actors.dino.state, "put_on");
  assert.deepEqual(item(s, "socks1"), { id: "socks1", kind: "socks", color: "red", worn_by: "dino" }, "worn items leave their old place");
});

test("put_on does not wear things that are not wearable or not there, and never twice", () => {
  const s = applyOps(initialScene(), [
    add("ball", "ball1"),
    add("hat", "hat1"),
    { op: "act", actor: "dino", action: "put_on", target: "ball1" },
    { op: "act", actor: "dino", action: "put_on", target: "ghost" },
    { op: "act", actor: "dino", action: "put_on" },
    { op: "act", actor: "dino", action: "put_on", target: "hat1" },
    { op: "act", actor: "dino", action: "put_on", target: "hat1" },
  ], CAT);
  assert.deepEqual(s.actors.dino.wearing, ["hat1"]);
  assert.equal(item(s, "ball1").worn_by, undefined);
});

test("chomp eats its food target: the food is gone from items and the wire, Chomp's state is chomp", () => {
  const s = applyOps(initialScene(), [add("apple", "apple1"), add("cookie", "cookie1"), { op: "act", actor: "chomp", action: "chomp", target: "apple1" }], CAT);
  assert.equal(item(s, "apple1"), undefined);
  assert.ok(item(s, "cookie1"), "only the target is eaten");
  assert.equal(s.actors.chomp.state, "chomp");
  assert.equal(toWire(s).some((entry) => entry.id === "apple1"), false);
});

test("chomp on something that is not food, or acting on food any other way, eats nothing", () => {
  const s = applyOps(initialScene(), [
    add("ball", "ball1"), add("apple", "apple1"),
    { op: "act", actor: "chomp", action: "chomp", target: "ball1" },
    { op: "act", actor: "chomp", action: "bark", target: "apple1" },
    { op: "act", actor: "dino", action: "kick", target: "apple1" },
    { op: "act", actor: "dino", action: "chomp", target: "apple1" }, // the dino has no chomp action
  ], CAT);
  assert.ok(item(s, "ball1") && item(s, "apple1"));
  assert.deepEqual(ids(s), ["nest1", "egg1", "ball1", "apple1"]);
});

test("chomp with a missing or unknown target changes only Chomp's state", () => {
  const base = applyOps(initialScene(), [add("apple", "apple1")], CAT);
  for (const target of [undefined, null, "ghost", "dino", "chomp", 7, {}]) {
    const s = applyOps(base, [{ op: "act", actor: "chomp", action: "chomp", ...(target === undefined ? {} : { target }) }], CAT);
    assert.deepEqual(s.items, base.items, String(target));
    assert.equal(s.actors.chomp.state, "chomp", String(target));
  }
});

test("eating food frees what sat on it: the place that pointed at the food is dropped", () => {
  const s = applyOps(initialScene(), [
    add("banana", "banana1"),
    add("ball", "ball1", { place: { rel: "on", ref: "banana1" } }),
    add("ball", "ball2", { place: { rel: "next_to", ref: "nest1" } }),
    { op: "act", actor: "chomp", action: "chomp", target: "banana1" },
  ], CAT);
  assert.equal(item(s, "banana1"), undefined);
  assert.deepEqual(item(s, "ball1"), { id: "ball1", kind: "ball" });
  assert.deepEqual(item(s, "ball2").place, { rel: "next_to", ref: "nest1" });
});

test("eaten food does not pile up toward the cap, and eating stays pure", () => {
  let s = initialScene();
  for (let i = 1; i <= 30; i += 1) {
    s = applyOps(s, [add("apple", `apple${i}`), { op: "act", actor: "chomp", action: "chomp", target: `apple${i}` }], CAT);
  }
  assert.deepEqual(ids(s), ["nest1", "egg1"]);
  const frozen = deepFreeze(applyOps(initialScene(), [add("apple", "apple1"), add("ball", "ball1", { place: { rel: "on", ref: "apple1" } })], CAT));
  const eaten = applyOps(frozen, deepFreeze([{ op: "act", actor: "chomp", action: "chomp", target: "apple1" }]), CAT);
  assert.ok(item(frozen, "apple1"), "the input scene still has its apple");
  assert.deepEqual(item(frozen, "ball1").place, { rel: "on", ref: "apple1" });
  assert.equal(item(eaten, "apple1"), undefined);
});

test("remove deletes an item; unknown targets and actors are ignored", () => {
  const s = applyOps(initialScene(), [add("ball", "ball1"), { op: "remove", target: "ball1" }, { op: "remove", target: "ball1" }, { op: "remove", target: "dino" }, { op: "remove", target: "chomp" }, { op: "remove" }], CAT);
  assert.deepEqual(ids(s), ["nest1", "egg1"]);
});

test("remove of a worn item also clears it from the wearing list", () => {
  let s = applyOps(initialScene(), [add("socks", "socks1"), add("hat", "hat1"), { op: "act", actor: "dino", action: "put_on", target: "socks1" }, { op: "act", actor: "dino", action: "put_on", target: "hat1" }], CAT);
  assert.deepEqual(s.actors.dino.wearing, ["socks1", "hat1"]);
  s = applyOps(s, [{ op: "remove", target: "socks1" }], CAT);
  assert.deepEqual(s.actors.dino.wearing, ["hat1"]);
  assert.equal(item(s, "socks1"), undefined);
});

test("removing something takes its dependents' place away so no reference dangles", () => {
  const s = applyOps(initialScene(), [add("tree", "tree1"), add("ball", "ball1", { place: { rel: "on", ref: "tree1" } }), { op: "remove", target: "tree1" }], CAT);
  assert.deepEqual(item(s, "ball1"), { id: "ball1", kind: "ball" });
});

test("set changes color and size of items the catalog lets have them", () => {
  const s = applyOps(initialScene(), [
    add("ball", "ball1"),
    add("apple", "apple1"),
    { op: "set", target: "ball1", color: "green", size: "small" },
    { op: "set", target: "apple1", color: "red" },
    { op: "set", target: "ball1", color: "magenta" },
    { op: "set", target: "ghost", color: "red" },
    { op: "set", target: "ball1", face: "happy" },
  ], CAT);
  assert.deepEqual(item(s, "ball1"), { id: "ball1", kind: "ball", color: "green", size: "small" });
  assert.deepEqual(item(s, "apple1"), { id: "apple1", kind: "apple" });
});

test("set face changes an actor's face only to one of that actor's faces", () => {
  const s = applyOps(initialScene(), [
    { op: "set", target: "chomp", face: "excited" },
    { op: "set", target: "dino", face: "sleepy" },
    { op: "set", target: "dino", face: "angry" },
    { op: "set", target: "chomp", color: "red" },
  ], CAT);
  assert.equal(s.actors.chomp.face, "excited");
  assert.equal(s.actors.dino.face, "sleepy");
  const rejected = applyOps(initialScene(), [{ op: "set", target: "dino", face: "angry" }], CAT);
  assert.equal(rejected.actors.dino.face, "happy");
});

test("move changes the place; a bad place, a loop or an actor is ignored", () => {
  const base = applyOps(initialScene(), [add("ball", "ball1"), add("block", "block1", { place: { rel: "on", ref: "ball1" } })], CAT);
  const moved = applyOps(base, [{ op: "move", target: "ball1", place: { rel: "next_to", ref: "nest1" } }], CAT);
  assert.deepEqual(item(moved, "ball1").place, { rel: "next_to", ref: "nest1" });
  const zoned = applyOps(base, [{ op: "move", target: "ball1", place: { zone: "right" } }], CAT);
  assert.deepEqual(item(zoned, "ball1").place, { zone: "right" });
  const ignored = applyOps(base, [
    { op: "move", target: "ball1", place: { zone: "moon" } },
    { op: "move", target: "ball1", place: { rel: "on", ref: "ghost" } },
    { op: "move", target: "ball1", place: { rel: "on", ref: "ball1" } },
    { op: "move", target: "ball1", place: { rel: "under", ref: "block1" } }, // block1 is on ball1: a loop
    { op: "move", target: "ghost", place: { zone: "left" } },
    { op: "move", target: "dino", place: { zone: "left" } },
    { op: "move", target: "ball1" },
  ], CAT);
  assert.deepEqual(ignored, base);
});

test("moving a worn item takes it off the actor", () => {
  let s = applyOps(initialScene(), [add("hat", "hat1"), { op: "act", actor: "dino", action: "put_on", target: "hat1" }], CAT);
  s = applyOps(s, [{ op: "move", target: "hat1", place: { zone: "sky" } }], CAT);
  assert.deepEqual(s.actors.dino.wearing, []);
  assert.deepEqual(item(s, "hat1"), { id: "hat1", kind: "hat", place: { zone: "sky" } });
});

test("unknown ops are ignored", () => {
  const s0 = initialScene();
  const s = applyOps(s0, [{ op: "explode" }, { op: "add" }, null, 5, "add", [], { kind: "ball" }, { op: 7 }, { op: "constructor" }], CAT);
  assert.deepEqual(s, s0);
  assert.deepEqual(applyOps(s0, undefined, CAT), s0);
  assert.deepEqual(applyOps(s0, { op: "add" }, CAT), s0);
});

test("scene keeps at most 20 items, never removing nest or tree", () => {
  const ops = [add("tree", "tree1"), ...Array.from({ length: 25 }, (_, i) => add("ball", `ball${i + 1}`))];
  const s = applyOps(initialScene(), ops, CAT);
  assert.equal(s.items.length, 20);
  assert.ok(item(s, "nest1"));
  assert.ok(item(s, "tree1"));
  assert.equal(item(s, "ball1"), undefined, "the oldest ball went first");
  assert.ok(item(s, "ball25"), "the newest ball stays");
});

test("the oldest go first, and the cap applies after every add", () => {
  let s = initialScene();
  for (let i = 1; i <= 18; i += 1) s = applyOps(s, [add("ball", `ball${i}`)], CAT);
  assert.equal(s.items.length, 20);
  s = applyOps(s, [add("cookie", "cookie1")], CAT);
  assert.equal(s.items.length, 20);
  assert.ok(item(s, "egg1") === undefined, "egg1 is the oldest unprotected item");
  assert.ok(item(s, "nest1"));
  assert.ok(item(s, "ball1"));
});

test("worn items are never dropped by the cap", () => {
  let s = applyOps(initialScene(), [add("socks", "socks1"), { op: "act", actor: "dino", action: "put_on", target: "socks1" }], CAT);
  s = applyOps(s, Array.from({ length: 30 }, (_, i) => add("ball", `ball${i + 1}`)), CAT);
  assert.equal(s.items.length, 20);
  assert.ok(item(s, "socks1"));
  assert.deepEqual(s.actors.dino.wearing, ["socks1"]);
});

test("a flood of trees still ends at 20 items: nest and tree only go when nothing else is left, trees before the nest", () => {
  let s = initialScene();
  for (let i = 0; i < 6; i += 1) s = applyOps(s, [{ op: "add", kind: "tree", count: 5 }], CAT);
  assert.equal(s.items.length, 20);
  assert.ok(item(s, "nest1"), "the starting nest outlives the trees");
  assert.equal(s.items.filter((one) => one.kind === "tree").length, 19);
  assert.equal(item(s, "egg1"), undefined, "ordinary items went first");
  assert.ok(item(s, "tree30"), "the newest tree stays");
});

test("evicted items leave no dangling place references behind", () => {
  let s = applyOps(initialScene(), [add("ball", "ball1"), add("block", "block1", { place: { rel: "on", ref: "ball1" } })], CAT);
  s = applyOps(s, Array.from({ length: 30 }, (_, i) => add("cookie", `cookie${i + 1}`)), CAT);
  for (const one of s.items) {
    if (one.place && one.place.ref) assert.ok(item(s, one.place.ref), `${one.id} points at a missing ${one.place.ref}`);
  }
});

test("toWire lists the actors first and then the items in the shape /api/turn expects", () => {
  let s = applyOps(initialScene(), [
    add("ball", "ball1", { color: "red", size: "big", place: { zone: "right" } }),
    add("socks", "socks1", { color: "blue" }),
    { op: "act", actor: "dino", action: "hatch" },
    { op: "act", actor: "dino", action: "put_on", target: "socks1" },
    { op: "set", target: "chomp", face: "excited" },
    { op: "act", actor: "chomp", action: "bark" },
  ], CAT);
  const wire = toWire(s);
  assert.deepEqual(wire.slice(0, 2), [
    { id: "chomp", kind: "chomp", state: "bark", face: "excited" },
    { id: "dino", kind: "dino", state: "put_on", face: "happy", hatched: true },
  ]);
  assert.deepEqual(wire.slice(2), [
    { id: "nest1", kind: "nest", place: { zone: "left" } },
    { id: "ball1", kind: "ball", color: "red", size: "big", place: { zone: "right" } },
    { id: "socks1", kind: "socks", color: "blue", state: "worn" },
  ]);
  JSON.stringify(wire); // plain data
  wire[2].place.zone = "sky";
  assert.deepEqual(item(s, "nest1").place, { zone: "left" }, "the wire shares nothing with the scene");
});

test("toWire of the first scene: actors, nest and the egg in it", () => {
  assert.deepEqual(toWire(initialScene()), [
    { id: "chomp", kind: "chomp", state: "idle", face: "happy" },
    { id: "dino", kind: "dino", state: "idle", face: "happy", hatched: false },
    { id: "nest1", kind: "nest", place: { zone: "left" } },
    { id: "egg1", kind: "egg", place: { rel: "in", ref: "nest1" } },
  ]);
});
