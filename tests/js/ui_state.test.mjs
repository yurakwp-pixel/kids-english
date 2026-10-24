import { test } from "node:test";
import assert from "node:assert/strict";
import { uiReducer, initialUiState } from "../../web/js/ui_state.js";

test("mic tap is ignored while thinking or speaking", () => {
  for (const s of ["thinking", "speaking"]) assert.equal(uiReducer({ mode: s }, { type: "TAP_MIC" }).mode, s);
  assert.equal(uiReducer({ mode: "idle" }, { type: "TAP_MIC" }).mode, "listening");
});

test("reply then tts end returns to idle", () => {
  let s = uiReducer({ mode: "thinking" }, { type: "LINE_REPLY", say: "Hi!" });
  assert.equal(s.mode, "speaking"); assert.equal(uiReducer(s, { type: "TTS_END" }).mode, "idle");
});

test("initial state is idle with turn 0", () => {
  assert.equal(initialUiState.mode, "idle");
  assert.equal(initialUiState.turnId, 0);
});

test("a second tap while listening keeps listening (no restart)", () => {
  const s = uiReducer(initialUiState, { type: "TAP_MIC" });
  assert.equal(uiReducer(s, { type: "TAP_MIC" }), s);
});

test("final mic result starts the next numbered turn and shows what was heard", () => {
  let s = uiReducer(initialUiState, { type: "TAP_MIC" });
  s = uiReducer(s, { type: "MIC_RESULT", alts: [{ t: "hello chomp", c: 0.9 }, { t: "hello champ", c: 0.5 }] });
  assert.equal(s.mode, "thinking");
  assert.equal(s.turnId, 1);
  assert.equal(s.heard, "hello chomp");
});

test("interim result only updates the heard text while listening", () => {
  const listening = uiReducer(initialUiState, { type: "TAP_MIC" });
  const s = uiReducer(listening, { type: "MIC_RESULT", final: false, alts: [{ t: "hel", c: 0 }] });
  assert.equal(s.mode, "listening");
  assert.equal(s.heard, "hel");
  const thinking = { ...initialUiState, mode: "thinking", turnId: 3, heard: "hi" };
  assert.equal(uiReducer(thinking, { type: "MIC_RESULT", final: false, alts: [{ t: "x", c: 0 }] }), thinking);
});

test("an empty final result still becomes a turn (server answers 'Say it again!')", () => {
  const s = uiReducer(uiReducer(initialUiState, { type: "TAP_MIC" }), { type: "MIC_RESULT", alts: [] });
  assert.equal(s.mode, "thinking");
  assert.equal(s.heard, "");
});

test("debug text input can start a turn from idle", () => {
  assert.equal(uiReducer(initialUiState, { type: "MIC_RESULT", alts: [{ t: "hi", c: 1 }] }).mode, "thinking");
});

test("no overlapping turns: results arriving while thinking or speaking are ignored", () => {
  for (const mode of ["thinking", "speaking"]) {
    const s = { ...initialUiState, mode, turnId: 4 };
    const next = uiReducer(s, { type: "MIC_RESULT", alts: [{ t: "again", c: 1 }] });
    assert.equal(next, s, mode);
  }
});

test("mic end without a result goes back to idle, but not after the turn started", () => {
  const listening = uiReducer(initialUiState, { type: "TAP_MIC" });
  assert.equal(uiReducer(listening, { type: "MIC_END" }).mode, "idle");
  const thinking = { ...initialUiState, mode: "thinking", turnId: 1 };
  assert.equal(uiReducer(thinking, { type: "MIC_END" }), thinking);
});

test("ack and fast lines keep thinking; a stale reply for another turn is ignored", () => {
  const thinking = { ...initialUiState, mode: "thinking", turnId: 2 };
  const acked = uiReducer(thinking, { type: "LINE_ACK", turn_id: 2 });
  assert.equal(acked.mode, "thinking");
  assert.equal(acked.acked, true);
  assert.equal(uiReducer(thinking, { type: "LINE_FAST", turn_id: 2, ops: [] }).mode, "thinking");
  assert.equal(uiReducer(thinking, { type: "LINE_REPLY", turn_id: 1, say: "old" }), thinking);
});

test("reply sets the caption and face; tts end of another turn is ignored", () => {
  const thinking = { ...initialUiState, mode: "thinking", turnId: 2 };
  const s = uiReducer(thinking, { type: "LINE_REPLY", turn_id: 2, say: "Hello! Let's play!", face: "excited" });
  assert.equal(s.mode, "speaking");
  assert.equal(s.say, "Hello! Let's play!");
  assert.equal(s.face, "excited");
  assert.equal(uiReducer(s, { type: "TTS_END", turn_id: 1 }), s);
  assert.equal(uiReducer(s, { type: "TTS_END", turn_id: 2 }).mode, "idle");
});

test("reply outside thinking is ignored", () => {
  for (const mode of ["idle", "listening", "speaking"]) {
    const s = { ...initialUiState, mode };
    assert.equal(uiReducer(s, { type: "LINE_REPLY", say: "late" }), s, mode);
  }
});

test("mic error while listening returns to idle with a notice; a tap clears it", () => {
  const listening = uiReducer(initialUiState, { type: "TAP_MIC" });
  const s = uiReducer(listening, { type: "ERROR", code: "mic-blocked" });
  assert.equal(s.mode, "idle");
  assert.equal(s.notice, "mic-blocked");
  assert.equal(uiReducer(s, { type: "TAP_MIC" }).notice, null);
});

test("errors never unlock the mic while a turn is in flight", () => {
  for (const mode of ["thinking", "speaking"]) {
    const s = { ...initialUiState, mode, turnId: 1 };
    assert.equal(uiReducer(s, { type: "ERROR", code: "network" }), s, mode);
  }
});

test("reducer does not mutate its input and ignores unknown events", () => {
  const s = Object.freeze({ ...initialUiState });
  assert.equal(uiReducer(s, { type: "NOPE" }), s);
  assert.equal(uiReducer(s, null), s);
  uiReducer(s, { type: "TAP_MIC" });
  assert.equal(s.mode, "idle");
});

// ---------------------------------------------------------------- (?) 힌트 (Task 15)

const replied = (hint = "공을 차 보자 → Kick the ball!") =>
  uiReducer({ ...initialUiState, mode: "thinking", turnId: 1 }, { type: "LINE_REPLY", turn_id: 1, say: "Kick the ball!", hint });

test("the reply's korean_hint is kept, hidden until (?) is tapped", () => {
  const s = replied();
  assert.equal(s.hint, "공을 차 보자 → Kick the ball!");
  assert.equal(s.hintShown, false);
  assert.equal(replied(null).hint, "");
  assert.equal(replied(42).hint, "");
});

test("tapping (?) shows the hint without starting a turn (no server call)", () => {
  for (const mode of ["speaking", "idle", "listening"]) {
    const s = { ...replied(), mode };
    const tapped = uiReducer(s, { type: "TAP_HINT" });
    assert.equal(tapped.hintShown, true, mode);
    assert.equal(tapped.mode, mode, mode);
    assert.equal(tapped.turnId, s.turnId, mode);
  }
});

test("(?) does nothing without a hint or while thinking (that hint belongs to the last turn)", () => {
  const none = { ...initialUiState };
  assert.equal(uiReducer(none, { type: "TAP_HINT" }), none);
  const thinking = { ...replied(), mode: "thinking" };
  assert.equal(uiReducer(thinking, { type: "TAP_HINT" }), thinking);
  const shown = uiReducer({ ...replied(), mode: "idle" }, { type: "TAP_HINT" });
  assert.equal(uiReducer(shown, { type: "TAP_HINT" }), shown); // 이미 보이면 그대로
});

test("the next turn carries hint_requested once, then the hint hides", () => {
  let s = uiReducer({ ...replied(), mode: "idle" }, { type: "TAP_HINT" });
  s = uiReducer(s, { type: "TAP_MIC" });
  assert.equal(s.hintShown, true); // 듣는 동안에도 보고 따라 말할 수 있다
  s = uiReducer(s, { type: "MIC_RESULT", final: true, alts: [{ t: "kick the ball", c: 0.9 }] });
  assert.equal(s.mode, "thinking");
  assert.equal(s.hintRequested, true);
  assert.equal(s.hintShown, false);
  s = uiReducer(s, { type: "LINE_REPLY", turn_id: s.turnId, say: "Good job!", hint: "잘했어 → Good job!" });
  s = uiReducer(s, { type: "TTS_END", turn_id: s.turnId });
  s = uiReducer(uiReducer(s, { type: "TAP_MIC" }), { type: "MIC_RESULT", final: true, alts: [] });
  assert.equal(s.hintRequested, false); // 이번에는 누르지 않았다
});

test("the initial state has no hint and asks for none", () => {
  assert.equal(initialUiState.hint, "");
  assert.equal(initialUiState.hintShown, false);
  assert.equal(initialUiState.hintRequested, false);
});
