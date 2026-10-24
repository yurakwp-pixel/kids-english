// createTts 를 가짜 speechSynthesis 로 시험한다(브라우저 없이).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createTts } from "../../web/js/tts.js";

const ANA = { name: "Microsoft Ana Online (Natural) - English (United States)", lang: "en-US" };

class FakeUtterance {
  constructor(text) { this.text = text; this.volume = 1; }
}

function fakeSynth(voices = [ANA]) {
  const listeners = new Set();
  return {
    voices,
    calls: [],
    spoken: [],
    getVoices() { return this.voices; },
    addEventListener(type, fn) { if (type === "voiceschanged") listeners.add(fn); },
    removeEventListener(type, fn) { listeners.delete(fn); },
    fireVoicesChanged() { for (const fn of [...listeners]) fn(); },
    cancel() { this.calls.push("cancel"); },
    speak(u) { this.calls.push("speak"); this.spoken.push(u); },
  };
}

test("speak cancels first, then speaks en-US at rate 0.9 with the picked voice, and resolves on end", async () => {
  const synth = fakeSynth();
  const tts = createTts({ synth, Utterance: FakeUtterance });
  const done = tts.speak("Hello! I'm Chomp!");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(synth.calls, ["cancel", "speak"]);
  const u = synth.spoken[0];
  assert.equal(u.text, "Hello! I'm Chomp!");
  assert.equal(u.rate, 0.9);
  assert.equal(u.lang, "en-US");
  assert.equal(u.voice, ANA);
  u.onend();
  await done;
});

test("speak resolves on an utterance error too", async () => {
  const synth = fakeSynth();
  const done = createTts({ synth, Utterance: FakeUtterance }).speak("Hi!");
  await new Promise((r) => setTimeout(r, 0));
  synth.spoken[0].onerror({ error: "synthesis-failed" });
  await done;
});

test("speak resolves by itself if the browser never reports the end", async () => {
  const synth = fakeSynth();
  const tts = createTts({ synth, Utterance: FakeUtterance, maxSpeakMs: () => 20 });
  await tts.speak("Hi!"); // 끝 알림이 없어도 멈춰 있지 않는다
});

test("speak waits for voiceschanged when no voices are loaded yet", async () => {
  const synth = fakeSynth([]);
  const tts = createTts({ synth, Utterance: FakeUtterance, voiceWaitMs: 2000 });
  const done = tts.speak("Hi!");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(synth.calls, []); // 아직 기다린다
  synth.voices = [ANA];
  synth.fireVoicesChanged();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(synth.spoken[0].voice, ANA);
  synth.spoken[0].onend();
  await done;
});

test("speak stays silent when no voice exists after the wait limit (caption only, no long mic lock)", async () => {
  const synth = fakeSynth([]);
  const tts = createTts({ synth, Utterance: FakeUtterance, voiceWaitMs: 20 });
  await tts.speak("Hi!");
  assert.equal(synth.spoken.length, 0);
});

test("speak stays silent when only non-English voices exist (no Korean voice reading English)", async () => {
  const synth = fakeSynth([{ name: "Microsoft Heami - Korean (Korean)", lang: "ko-KR" }]);
  await createTts({ synth, Utterance: FakeUtterance }).speak("Hi!");
  assert.equal(synth.spoken.length, 0);
});

test("speak uses an en-GB voice when it is the only English one", async () => {
  const gb = { name: "Microsoft Libby Online (Natural) - English (United Kingdom)", lang: "en-GB" };
  const synth = fakeSynth([{ name: "Heami", lang: "ko-KR" }, gb]);
  const done = createTts({ synth, Utterance: FakeUtterance }).speak("Hi!");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(synth.spoken[0].voice, gb);
  assert.equal(synth.spoken[0].lang, "en-GB");
  synth.spoken[0].onend();
  await done;
});

test("without speechSynthesis speak resolves silently and cancel is safe", async () => {
  const tts = createTts({ synth: undefined, Utterance: undefined });
  await tts.speak("Hi!");
  tts.cancel();
  tts.warm();
});

test("cancel ends the current utterance's promise", async () => {
  const synth = fakeSynth();
  const tts = createTts({ synth, Utterance: FakeUtterance });
  const done = tts.speak("Hello there!");
  await new Promise((r) => setTimeout(r, 0));
  tts.cancel();
  await done;
  assert.equal(synth.calls.at(-1), "cancel");
});

test("warm speaks one silent utterance to unlock speech", () => {
  const synth = fakeSynth();
  createTts({ synth, Utterance: FakeUtterance }).warm();
  assert.equal(synth.spoken.length, 1);
  assert.equal(synth.spoken[0].volume, 0);
});
