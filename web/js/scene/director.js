// 장면 연출 순서. 서버 한 줄의 ops 를 "보이는 순서"로 나눠 차례로 장면 모델에 적용하고, 동작이 끝나면 쉬는 자세로 돌린다.
// DOM 을 모른다(그리기는 onChange 를 받은 main.js → render.js). 시계를 주입받아 node 로 시험한다(tests/js/director.test.mjs).
//
//   "kick the ball" 첫 턴의 1줄 [hatch, add ball1, act kick ball1]
//     0 ms     hatch      알이 흔들리고 깨지고 공룡이 튀어나온다(render.js, 약 1초)
//     1100 ms  add ball1  공이 퐁 생긴다
//     1550 ms  act kick   공룡이 찬다 … 1400 ms 뒤 local "act idle" 로 쉬는 자세(toWire 가 더는 "kick" 이라고 하지 않는다)
//
// 단계 나누기(stageOps): op 순서는 그대로 두고, hatch 는 혼자 한 단계, 이어진 add 들이 한 단계, 이어진 나머지(act/set/move/
// remove)가 한 단계다. 단계마다 기다리는 시간은 HATCH_MS / ADD_MS / ACT_MS, 마지막 단계는 0.
// 2줄(reply)의 ops 는 1줄 단계가 다 끝난 뒤에 이어서 나온다(play 는 줄을 서서 기다린다).
// flush(): 남은 단계와 쉬는 자세를 지금 다 적용한다. 다음 턴 요청에는 언제나 최종 장면이 실린다.
import { applyOps, initialScene } from "./model.js";

export const HATCH_MS = 1100; // 흔들기 0.5초 + 금 + 깨지며 튀어나오기 → 공룡이 자리에 선다
export const ADD_MS = 450; // 물건이 퐁 생기는 시간(0.38초)보다 조금 길게
export const ACT_MS = 800; // 앞 동작(덥석, 차기)이 보이고 나서 다음 단계

const ACTORS = ["chomp", "dino"];
const HOLD_MS = { hatch: 1300, hide: 2500, sleep: 3000, go_to_bed: 3000 };
const DEFAULT_HOLD_MS = 1400;
const WAIT = { hatch: HATCH_MS, add: ADD_MS, act: ACT_MS };

/** 동작을 보여 주는 시간. 이만큼 지나면 그 캐릭터는 쉬는 자세(act idle)로 돌아간다. */
export function actionHoldMs(action) {
  return typeof action === "string" && Object.hasOwn(HOLD_MS, action) ? HOLD_MS[action] : DEFAULT_HOLD_MS;
}

const isOp = (op) => op !== null && typeof op === "object" && !Array.isArray(op) && typeof op.op === "string";
const kindOf = (op) => (op.op === "act" && op.action === "hatch" ? "hatch" : op.op === "add" ? "add" : "act");

/** ops → [{ ops, wait }]. op 의 순서는 바뀌지 않는다. */
export function stageOps(ops) {
  const steps = [];
  for (const op of Array.isArray(ops) ? ops : []) {
    if (!isOp(op)) continue;
    const kind = kindOf(op);
    const last = steps.at(-1);
    if (last && kind !== "hatch" && last.kind === kind) last.ops.push(op);
    else steps.push({ kind, ops: [op] });
  }
  return steps.map((step, i) => ({ ops: step.ops, wait: i === steps.length - 1 ? 0 : WAIT[step.kind] }));
}

/**
 * onChange(scene, ops, source) 는 단계 하나가 적용될 때마다 불린다. source: play 에 준 이름("fast", "reply"), 쉬는 자세는 "local".
 * timers 는 { setTimeout, clearTimeout } (기본은 전역).
 */
export function createDirector({ catalog = {}, scene = initialScene(), onChange = () => {}, timers = globalThis } = {}) {
  let cat = catalog ?? {};
  let current = scene;
  const queue = [];
  let stepTimer = null;
  let busy = false;
  let waiters = [];
  const rests = new Map(); // actor -> { action, timer }

  const later = (fn, ms) => timers.setTimeout(fn, ms);
  const cancel = (id) => {
    if (id !== null && id !== undefined) timers.clearTimeout(id);
  };

  function apply(ops, source) {
    current = applyOps(current, ops, cat);
    onChange(current, ops, source);
    for (const op of ops) {
      // 모델이 받아들인 동작만(새 장면에서 그 캐릭터의 상태가 그 동작이다)
      if (op.op !== "act" || op.action === "idle" || !ACTORS.includes(op.actor)) continue;
      if (current.actors[op.actor]?.state === op.action) rest(op.actor, op.action);
    }
  }

  function rest(actor, action) {
    cancel(rests.get(actor)?.timer);
    const timer = later(() => {
      rests.delete(actor);
      if (current.actors[actor]?.state === action) apply([{ op: "act", actor, action: "idle" }], "local");
    }, actionHoldMs(action));
    rests.set(actor, { action, timer });
  }

  function settle() {
    busy = false;
    const done = waiters;
    waiters = [];
    for (const fn of done) fn();
  }

  function runNext() {
    stepTimer = null;
    const step = queue.shift();
    if (!step) return settle();
    apply(step.ops, step.source);
    if (step.wait > 0 || queue.length > 0) stepTimer = later(runNext, step.wait);
    else settle();
  }

  return {
    get scene() {
      return current;
    },
    get busy() {
      return busy;
    },
    setCatalog(next) {
      cat = next ?? {};
    },
    play(ops, source = "fast") {
      const steps = stageOps(ops);
      if (!steps.length) return;
      queue.push(...steps.map((step) => ({ ...step, source })));
      if (!busy) {
        busy = true;
        runNext();
      }
    },
    flush() {
      cancel(stepTimer);
      stepTimer = null;
      while (queue.length) {
        const step = queue.shift();
        apply(step.ops, step.source);
      }
      const resets = [];
      for (const [actor, pending] of rests) {
        cancel(pending.timer);
        if (current.actors[actor]?.state === pending.action) resets.push({ op: "act", actor, action: "idle" });
      }
      rests.clear();
      if (resets.length) apply(resets, "local");
      settle();
      return current;
    },
    /** 남은 단계가 모두 적용되면(마지막 단계의 움직임이 시작되면), 늦어도 maxMs 뒤에 풀린다. */
    whenIdle(maxMs = Infinity) {
      if (!busy || !(maxMs > 0)) return Promise.resolve();
      return new Promise((resolve) => {
        let timer = null;
        const done = () => {
          cancel(timer);
          resolve();
        };
        waiters.push(done);
        if (Number.isFinite(maxMs)) {
          timer = later(() => {
            waiters = waiters.filter((fn) => fn !== done);
            resolve();
          }, maxMs);
        }
      });
    },
  };
}
