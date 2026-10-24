// 장면 연출 순서(web/js/scene/director.js): 한 줄의 ops 를 단계로 나누고, 차례로 적용하고, 동작이 끝나면 쉬는 자세로 돌린다.
// 시계는 가짜(fakeClock)라 기다리지 않고 시험한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stageOps, createDirector, actionHoldMs, HATCH_MS, ADD_MS, ACT_MS } from "../../web/js/scene/director.js";
import { initialScene, toWire } from "../../web/js/scene/model.js";

const CAT = JSON.parse(readFileSync(new URL("../../shared/catalog.json", import.meta.url), "utf8"));
const HATCH = { op: "act", actor: "dino", action: "hatch" };
const addBall = { op: "add", kind: "ball", id: "ball1", count: 1 };
const kick = { op: "act", actor: "dino", action: "kick", target: "ball1" };

function fakeClock() {
  let now = 0;
  let seq = 0;
  const due = new Map();
  return {
    timers: {
      setTimeout(fn, ms = 0) { seq += 1; due.set(seq, { at: now + Math.max(0, ms), fn, seq }); return seq; },
      clearTimeout(id) { due.delete(id); },
    },
    tick(ms) {
      const end = now + ms;
      for (;;) {
        const next = [...due.values()].filter((t) => t.at <= end).sort((a, b) => a.at - b.at || a.seq - b.seq)[0];
        if (!next) break;
        due.delete(next.seq);
        now = next.at;
        next.fn();
      }
      now = end;
    },
    get pending() { return due.size; },
  };
}

function setup(extra = {}) {
  const clock = fakeClock();
  const calls = [];
  const director = createDirector({
    catalog: CAT,
    timers: clock.timers,
    onChange: (scene, ops, source) => calls.push({ ops, source, dino: scene.actors.dino.state, items: scene.items.map((i) => i.id) }),
    ...extra,
  });
  return { clock, calls, director };
}

// ---------------------------------------------------------------- stageOps

test("stageOps: hatch first on its own, then the adds, then what acts on them", () => {
  const steps = stageOps([HATCH, addBall, kick]);
  assert.deepEqual(steps, [
    { ops: [HATCH], wait: HATCH_MS },
    { ops: [addBall], wait: ADD_MS },
    { ops: [kick], wait: 0 },
  ]);
});

test("stageOps keeps op order: runs of adds and runs of other ops, the last step waits 0", () => {
  const chompApple = { op: "act", actor: "chomp", action: "chomp", target: "apple1" };
  const face = { op: "set", target: "chomp", face: "happy" };
  const apple = { op: "add", kind: "apple", id: "apple1" };
  const cookie = { op: "add", kind: "cookie", id: "cookie1" };
  const chompCookie = { op: "act", actor: "chomp", action: "chomp", target: "cookie1" };
  assert.deepEqual(stageOps([apple, chompApple, cookie, chompCookie, face]).map((s) => [s.ops, s.wait]), [
    [[apple], ADD_MS], [[chompApple], ACT_MS], [[cookie], ADD_MS], [[chompCookie, face], 0],
  ]);
  assert.deepEqual(stageOps([kick]), [{ ops: [kick], wait: 0 }]);
  assert.deepEqual(stageOps([addBall]), [{ ops: [addBall], wait: 0 }]);
});

test("stageOps ignores what is not an op list", () => {
  assert.deepEqual(stageOps(undefined), []);
  assert.deepEqual(stageOps([]), []);
  assert.deepEqual(stageOps([null, 3, "x"]), []);
});

test("the hatch step waits about a second (wobble, crack, pop out)", () => {
  assert.ok(HATCH_MS >= 900 && HATCH_MS <= 1300);
  assert.ok(ADD_MS >= 300 && ADD_MS <= 600);
});

// ---------------------------------------------------------------- 차례로 적용

test("play applies the first step now and the rest after each wait", () => {
  const { clock, calls, director } = setup();
  director.play([HATCH, addBall, kick], "fast");
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { ops: [HATCH], source: "fast", dino: "hatch", items: ["nest1"] });
  assert.equal(director.busy, true);
  clock.tick(HATCH_MS - 1);
  assert.equal(calls.length, 1);
  clock.tick(1);
  assert.deepEqual(calls[1].items, ["nest1", "ball1"]);
  clock.tick(ADD_MS);
  // 그 사이(1300 ms)에 부화 자세가 쉬는 자세로 돌아가고, 1550 ms 에 찬다
  assert.deepEqual(calls.map((c) => c.source), ["fast", "fast", "local", "fast"]);
  assert.deepEqual(calls.at(-1), { ops: [kick], source: "fast", dino: "kick", items: ["nest1", "ball1"] });
  assert.equal(director.busy, false);
});

test("a second play waits behind the first (line 2 after line 1)", () => {
  const { clock, calls, director } = setup();
  director.play([addBall, kick], "fast");
  director.play([{ op: "act", actor: "chomp", action: "jump" }], "reply");
  assert.equal(calls.length, 1);
  clock.tick(ADD_MS - 1);
  assert.equal(calls.length, 1);
  clock.tick(1);
  assert.deepEqual(calls.map((c) => [c.source, c.ops[0].action ?? c.ops[0].op]), [["fast", "add"], ["fast", "kick"], ["reply", "jump"]]);
  assert.equal(director.busy, false);
});

test("after an action is held, a local act idle puts the actor back at rest (toWire stops saying kick)", () => {
  const { clock, calls, director } = setup();
  director.play([HATCH], "fast");
  clock.tick(actionHoldMs("hatch"));
  assert.equal(calls.at(-1).source, "local");
  assert.deepEqual(calls.at(-1).ops, [{ op: "act", actor: "dino", action: "idle" }]);
  director.play([addBall, kick], "fast");
  clock.tick(ADD_MS);
  assert.equal(toWire(director.scene)[1].state, "kick");
  clock.tick(actionHoldMs("kick"));
  assert.equal(toWire(director.scene)[1].state, "idle");
  assert.equal(clock.pending, 0);
});

test("a newer action cancels the older rest timer, so the newer pose is not cut short", () => {
  const { clock, director } = setup();
  director.play([HATCH], "fast");
  clock.tick(HATCH_MS);
  director.play([{ op: "act", actor: "dino", action: "clap" }], "fast");
  clock.tick(actionHoldMs("clap") - 100);
  assert.equal(director.scene.actors.dino.state, "clap");
  clock.tick(100);
  assert.equal(director.scene.actors.dino.state, "idle");
});

test("rest timers are per actor", () => {
  const { clock, director } = setup();
  director.play([HATCH], "fast");
  clock.tick(HATCH_MS);
  director.play([{ op: "act", actor: "chomp", action: "jump" }, { op: "act", actor: "dino", action: "go_to_bed" }], "reply");
  clock.tick(actionHoldMs("jump"));
  assert.equal(director.scene.actors.chomp.state, "idle");
  assert.equal(director.scene.actors.dino.state, "go_to_bed");
  assert.ok(actionHoldMs("go_to_bed") > actionHoldMs("jump"));
});

test("flush applies every waiting step and rest at once (the next turn sends the final scene)", () => {
  const { clock, calls, director } = setup();
  director.play([HATCH, addBall, kick], "fast");
  const scene = director.flush();
  assert.deepEqual(scene.items.map((i) => i.id), ["nest1", "ball1"]);
  assert.equal(scene.actors.dino.hatched, true);
  assert.equal(scene.actors.dino.state, "idle");
  assert.equal(director.busy, false);
  assert.equal(clock.pending, 0);
  const sources = calls.map((c) => c.source);
  assert.deepEqual(sources, ["fast", "fast", "fast", "local"]);
});

test("whenIdle resolves when the last step has been applied, or at the cap", async () => {
  const { clock, director } = setup();
  let done = false;
  director.play([addBall, kick], "fast");
  const waiting = director.whenIdle(5000).then(() => { done = true; });
  await Promise.resolve();
  assert.equal(done, false);
  clock.tick(ADD_MS);
  await waiting;
  assert.equal(done, true);

  director.play([HATCH, addBall], "fast");
  let capped = false;
  const cap = director.whenIdle(100).then(() => { capped = true; });
  clock.tick(100);
  await cap;
  assert.equal(capped, true);
  assert.equal(director.busy, true);
  await director.whenIdle(0); // 0 은 바로 풀린다
});

test("whenIdle is immediate when nothing is playing", async () => {
  const { director } = setup();
  assert.equal(director.busy, false);
  await director.whenIdle(10_000);
});

test("ops the model cannot use change nothing and schedule nothing", () => {
  const { clock, calls, director } = setup();
  director.play([{ op: "act", actor: "dino", action: "fly" }], "reply");
  assert.equal(calls.length, 1);
  assert.equal(director.scene.actors.dino.state, "idle");
  assert.equal(clock.pending, 0);
});

test("the catalog can arrive after the director is made (ops before it change nothing)", () => {
  const clock = fakeClock();
  const director = createDirector({ timers: clock.timers });
  director.play([addBall], "fast");
  assert.deepEqual(director.scene.items.map((i) => i.id), ["nest1", "egg1"]);
  director.setCatalog(CAT);
  director.play([addBall], "fast");
  assert.ok(director.scene.items.some((i) => i.id === "ball1"));
  assert.deepEqual(createDirector({ timers: clock.timers }).scene, initialScene());
});
