// 아기 공룡의 동작 포즈 표(web/js/scene/poses.js): 동작 하나를 더할 때 그림이 아니라 데이터만 더한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { POSES, PARTS, poseFor } from "../../web/js/scene/poses.js";

const CAT = JSON.parse(readFileSync(new URL("../../shared/catalog.json", import.meta.url), "utf8"));
const MOVES = ["rot", "dx", "dy"];

test("every dino action in catalog has a pose", () => {
  for (const act of CAT.actors.dino.actions) assert.ok(POSES[act], act);
});

test("the pose table has a rest pose and nothing the catalog does not know", () => {
  assert.ok(POSES.idle);
  const known = new Set(["idle", ...CAT.actors.dino.actions]);
  for (const name of Object.keys(POSES)) assert.ok(known.has(name), `${name} is not a dino action`);
});

test("a pose only moves the seven named parts, by numbers in a sane range", () => {
  assert.deepEqual([...PARTS], ["body", "head", "armL", "armR", "legL", "legR", "tail"]);
  for (const [name, pose] of Object.entries(POSES)) {
    for (const [part, move] of Object.entries(pose)) {
      assert.ok(PARTS.includes(part), `${name}.${part}`);
      for (const [key, value] of Object.entries(move)) {
        assert.ok(MOVES.includes(key), `${name}.${part}.${key}`);
        assert.ok(Number.isFinite(value), `${name}.${part}.${key}`);
      }
      assert.ok(Math.abs(move.rot ?? 0) <= 180, `${name}.${part}.rot`);
      assert.ok(Math.abs(move.dx ?? 0) <= 60 && Math.abs(move.dy ?? 0) <= 60, `${name}.${part} offset`);
    }
  }
});

test("idle is the rest pose and every other action visibly moves something", () => {
  assert.deepEqual(POSES.idle, {});
  for (const act of CAT.actors.dino.actions) {
    const moves = Object.values(POSES[act]).flatMap((move) => MOVES.map((key) => move[key] ?? 0));
    assert.ok(moves.some((value) => value !== 0), `${act} looks like idle`);
  }
});

test("no two actions share the same pose", () => {
  const seen = new Map();
  for (const [name, pose] of Object.entries(POSES)) {
    const key = JSON.stringify(Object.keys(pose).sort().map((part) => [part, pose[part]]));
    assert.ok(!seen.has(key), `${name} is the same pose as ${seen.get(key)}`);
    seen.set(key, name);
  }
});

test("poseFor returns the pose of a known action and idle for anything else", () => {
  assert.equal(poseFor("kick"), POSES.kick);
  assert.equal(poseFor("hatch"), POSES.hatch);
  for (const odd of ["fly", "", undefined, null, 7, {}, "__proto__", "constructor", "toString", "hasOwnProperty"]) {
    assert.equal(poseFor(odd), POSES.idle, String(odd));
  }
});

test("poses are frozen so one animation cannot change another", () => {
  assert.throws(() => { POSES.kick.legR.rot = 0; }, TypeError);
  assert.throws(() => { POSES.kick.extra = {}; }, TypeError);
  assert.throws(() => { POSES.newAction = {}; }, TypeError);
});
