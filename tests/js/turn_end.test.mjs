// turn_end.js: 턴은 어떤 오류가 나도 "생각 중"에 갇히지 않고 답이나 대체 대사로 끝난다.
// main.js 의 runTurn 과 같은 순서로 쓴다: 답 줄이 오면 startShowing(연출 기다리기, LINE_REPLY), 마지막에 endTurn(대체 대사).
import { test } from "node:test";
import assert from "node:assert/strict";
import { startShowing, endTurn } from "../../web/js/turn_end.js";
import { uiReducer, initialUiState } from "../../web/js/ui_state.js";

const FALLBACK = "Hmm! Let's try that again!";

/** 리듀서만 쓰는 작은 화면: thinking 인 턴 1. afterReduce 는 main.js 의 renderUi/runEffects 자리(여기서 throw 해 본다). */
function thinkingScreen({ afterReduce = () => {} } = {}) {
  let state = uiReducer(initialUiState, { type: "MIC_RESULT", final: true, alts: [{ t: "kick the ball", c: 0.9 }] });
  const dispatch = (event) => {
    const prev = state;
    state = uiReducer(prev, event);
    if (state === prev) return false;
    afterReduce(state, event);
    return true;
  };
  return {
    get state() { return state; },
    dispatch,
    reply: (say = "Kick the ball!") => dispatch({ type: "LINE_REPLY", turn_id: 1, say, face: "happy", hint: "" }),
    fallback: () => dispatch({ type: "LINE_REPLY", turn_id: 1, say: FALLBACK, face: "happy", hint: "" }),
  };
}

test("the screen starts in thinking for turn 1", () => {
  assert.equal(thinkingScreen().state.mode, "thinking");
});

test("a reply that was shown ends the turn without the fallback", async () => {
  const screen = thinkingScreen();
  const errors = [];
  let fallbacks = 0;
  const showing = startShowing(async () => {}, () => screen.reply());
  assert.equal(await endTurn(showing, () => { fallbacks += 1; }, (e) => errors.push(e)), true);
  assert.equal(fallbacks, 0);
  assert.deepEqual(errors, []);
  assert.equal(screen.state.mode, "speaking");
  assert.equal(screen.state.say, "Kick the ball!");
});

test("no reply line at all (server down) ends in the fallback", async () => {
  const screen = thinkingScreen();
  assert.equal(await endTurn(null, screen.fallback), false);
  assert.equal(screen.state.mode, "speaking");
  assert.equal(screen.state.say, FALLBACK);
});

test("a reply the reducer did not take (dispatch returned false) ends in the fallback", async () => {
  const screen = thinkingScreen();
  const showing = startShowing(async () => {}, () => false);
  assert.equal(await endTurn(showing, screen.fallback), false);
  assert.equal(screen.state.say, FALLBACK);
});

for (const [name, wait, show] of [
  ["waiting for the scene throws synchronously", () => { throw new Error("whenIdle bug"); }, () => true],
  ["waiting for the scene rejects", () => Promise.reject(new Error("whenIdle bug")), () => true],
  ["the reply dispatch throws before the reducer", async () => {}, () => { throw new Error("dispatch bug"); }],
]) {
  test(`${name}: the turn still ends in the fallback and reports the error`, async () => {
    const screen = thinkingScreen();
    const errors = [];
    const showing = startShowing(wait, show);
    assert.equal(await endTurn(showing, screen.fallback, (e) => errors.push(e)), false);
    assert.equal(screen.state.mode, "speaking"); // 생각 중에 갇히지 않는다
    assert.equal(screen.state.say, FALLBACK);
    assert.equal(errors.length, 1);
    assert.match(errors[0].message, /bug/);
  });
}

test("drawing that throws after the reducer took the reply leaves speaking, not thinking", async () => {
  const screen = thinkingScreen({ afterReduce: (s) => { if (s.mode === "speaking") throw new Error("render bug"); } });
  const errors = [];
  const showing = startShowing(async () => {}, () => screen.reply());
  assert.equal(await endTurn(showing, screen.fallback, (e) => errors.push(e)), false);
  assert.equal(screen.state.mode, "speaking"); // 대체 대사는 리듀서가 받지 않는다(이미 말하는 중)
  assert.equal(screen.state.say, "Kick the ball!");
  assert.equal(errors.length, 1);
});

test("startShowing never rejects: a throw becomes { shown: false, error }", async () => {
  const boom = new Error("boom");
  assert.deepEqual(await startShowing(async () => {}, () => true), { shown: true });
  assert.deepEqual(await startShowing(async () => {}, () => "yes"), { shown: false }); // true 만 보인 것이다
  assert.deepEqual(await startShowing(() => { throw boom; }, () => true), { shown: false, error: boom });
  let called = false;
  await startShowing(() => Promise.reject(boom), () => { called = true; return true; });
  assert.equal(called, false); // 기다리기가 실패하면 답을 보이지 않는다(대체 대사가 대신한다)
});

test("endTurn never rejects, even when the fallback and the error report throw", async () => {
  const showing = startShowing(() => { throw new Error("first"); }, () => true);
  const result = await endTurn(showing, () => { throw new Error("fallback bug"); }, () => { throw new Error("report bug"); });
  assert.equal(result, false);
  assert.equal(await endTurn(Promise.reject(new Error("raw rejection")), () => {}), false);
});
