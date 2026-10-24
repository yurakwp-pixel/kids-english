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
