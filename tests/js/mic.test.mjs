// createMic 을 가짜 SpeechRecognition 으로 시험한다. 진짜 마이크 시험은 부모가 아이와 한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createMic } from "../../web/js/mic.js";

// 브라우저 SpeechRecognitionResultList 처럼 index 와 length 로 읽히는 모양
function resultList(...results) {
  return Object.assign([...results], {});
}
function result(isFinal, ...alts) {
  return Object.assign(alts.map(([transcript, confidence]) => ({ transcript, confidence })), { isFinal });
}

function fakeRecognitionClass({ local = "unavailable", throwOnStart = false } = {}) {
  class FakeRecognition {
    static instances = [];
    constructor() {
      this.processLocally = false;
      this.started = false;
      FakeRecognition.instances.push(this);
    }
    start() {
      if (throwOnStart) throw new Error("InvalidStateError");
      this.started = true;
      this.onstart?.();
    }
    stop() { this.onend?.(); }
    abort() { this.onerror?.({ error: "aborted" }); this.onend?.(); }
    // 시험용: 브라우저가 내는 사건 흉내
    emitResult(resultIndex, ...results) { this.onresult?.({ resultIndex, results: resultList(...results) }); }
    emitError(error) { this.onerror?.({ error }); }
    emitEnd() { this.onend?.(); }
  }
  if (local !== null) FakeRecognition.available = async () => local;
  return FakeRecognition;
}

function recorder() {
  const events = [];
  return {
    events,
    onResult: (alts) => events.push(["result", alts]),
    onInterim: (text) => events.push(["interim", text]),
    onState: (s, code) => events.push(code ? ["state", s, code] : ["state", s]),
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test("unsupported without SpeechRecognition", () => {
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...recorder(), SpeechRecognition: null });
  assert.equal(mic.supported, false);
  assert.equal(mic.engine, "none");
  mic.start(); // 아무 일도 없다
  mic.stop();
});

test("start configures one-shot English recognition with interim results", async () => {
  const SR = fakeRecognitionClass();
  const r = recorder();
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR });
  await tick();
  assert.equal(mic.supported, true);
  assert.equal(mic.engine, "webspeech");
  mic.start();
  const rec = SR.instances[0];
  assert.equal(rec.lang, "en-US");
  assert.equal(rec.continuous, false);
  assert.equal(rec.interimResults, true);
  assert.equal(rec.maxAlternatives, 5);
  assert.equal(rec.processLocally, false);
  assert.deepEqual(r.events, [["state", "listening"]]);
});

test("final result gives trimmed n-best once; the end then reports idle", async () => {
  const SR = fakeRecognitionClass();
  const r = recorder();
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR });
  mic.start();
  const rec = SR.instances[0];
  rec.emitResult(0, result(false, ["hel", 0]));
  rec.emitResult(0, result(true, [" hello chomp", 0.912], ["hello champ", 0.4], ["", 0.1]));
  rec.emitError("no-speech"); // 결과 뒤의 늦은 오류는 무시한다
  rec.emitEnd();
  assert.deepEqual(r.events, [
    ["state", "listening"],
    ["interim", "hel"],
    ["result", [{ t: "hello chomp", c: 0.91 }, { t: "hello champ", c: 0.4 }]],
    ["state", "idle"],
  ]);
});

test("ending with nothing heard gives an empty result (the server answers 'Say it again!')", () => {
  const SR = fakeRecognitionClass();
  const r = recorder();
  createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR }).start();
  const rec = SR.instances[0];
  rec.emitError("no-speech");
  rec.emitEnd();
  assert.deepEqual(r.events, [["state", "listening"], ["result", []], ["state", "idle"]]);
});

test("permission denied reports a friendly error code and no result", () => {
  const SR = fakeRecognitionClass();
  const r = recorder();
  createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR }).start();
  const rec = SR.instances[0];
  rec.emitError("not-allowed");
  rec.emitEnd();
  assert.deepEqual(r.events, [["state", "listening"], ["state", "error", "mic-blocked"], ["state", "idle"]]);
});

test("a start that throws reports an error instead of crashing", () => {
  const r = recorder();
  createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: fakeRecognitionClass({ throwOnStart: true }) }).start();
  assert.deepEqual(r.events, [["state", "error", "start-failed"]]);
});

test("stop aborts without delivering a result", () => {
  const SR = fakeRecognitionClass();
  const r = recorder();
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR });
  mic.start();
  mic.stop();
  assert.deepEqual(r.events, [["state", "listening"], ["state", "idle"]]);
});

test("start while the previous recognizer is still ending reports idle instead of hanging", () => {
  const SR = fakeRecognitionClass();
  const r = recorder();
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR });
  mic.start();
  SR.instances[0].emitResult(0, result(true, ["hi", 0.9])); // 결과는 왔지만 onend 는 아직
  mic.start();
  assert.equal(SR.instances.length, 1);
  assert.deepEqual(r.events.at(-1), ["state", "idle"]);
});

test("uses on-device recognition when available and reports webspeech-local", async () => {
  const SR = fakeRecognitionClass({ local: "available" });
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...recorder(), SpeechRecognition: SR });
  await tick();
  assert.equal(mic.engine, "webspeech-local");
  mic.start();
  assert.equal(SR.instances[0].processLocally, true);
});

test("falls back to the normal engine after an on-device failure", async () => {
  const SR = fakeRecognitionClass({ local: "available" });
  const r = recorder();
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: SR });
  await tick();
  mic.start();
  SR.instances[0].emitError("language-not-supported");
  SR.instances[0].emitEnd();
  assert.deepEqual(r.events.slice(1), [["result", []], ["state", "idle"]]); // 아이에게는 "다시 말해 볼까?"
  assert.equal(mic.engine, "webspeech");
  mic.start();
  assert.equal(SR.instances[1].processLocally, false);
});

test("an on-device status other than available keeps the normal engine", async () => {
  for (const local of ["downloadable", "unavailable", null]) {
    const SR = fakeRecognitionClass({ local });
    const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...recorder(), SpeechRecognition: SR });
    await tick();
    assert.equal(mic.engine, "webspeech", String(local));
  }
});

test("a recognizer that never ends after stop is let go on a later start (the mic does not stay dead)", () => {
  class Wedged {
    static instances = [];
    constructor() { Wedged.instances.push(this); }
    start() {}
    abort() {} // 브라우저가 끝(onend)을 끝내 알리지 않는다
    stop() {}
  }
  let clock = 1000;
  const r = recorder();
  const mic = createMic({ lang: "en-US", maxAlternatives: 5, ...r, SpeechRecognition: Wedged, now: () => clock });
  mic.start();
  mic.stop(); // 감시 시간이 지나 main.js 가 멈췄다
  clock += 500;
  mic.start(); // 막 멈춘 것은 아직 기다린다(겹쳐 시작하지 않는다)
  assert.equal(Wedged.instances.length, 1);
  assert.deepEqual(r.events.at(-1), ["state", "idle"]);
  clock += 1500;
  mic.start(); // 오래 끝나지 않은 것은 놓고 새로 듣는다
  assert.equal(Wedged.instances.length, 2);
  assert.deepEqual(r.events.at(-1), ["state", "listening"]);
  // 놓아 준 인식기가 늦게 무엇을 보내도 아무 일 없다
  const old = Wedged.instances[0];
  const count = r.events.length;
  old.onresult?.({ resultIndex: 0, results: resultList(result(true, ["late", 0.9])) });
  old.onend?.();
  assert.equal(r.events.length, count);
});
