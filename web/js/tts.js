// 멍멍이의 영어 말소리: 브라우저 speechSynthesis(en-US). 추가 비용 0원.
// kids-multiplication/index.html 의 speak()/pickVoice() 를 영어용으로 옮겼다.
// - 목소리가 없거나 speechSynthesis 가 없으면 조용히 넘어간다. 자막은 화면에 항상 있다.
// - 이 파일은 불러올 때 브라우저 객체를 건드리지 않는다(createTts 안에서만). 그래서 node 로 시험한다.

// 목소리 고르기(순수 함수): en-US "Online (Natural)" 중 Ana(아이 목소리) → 다른 en-US Natural → 아무 en-US → 아무 en-*.
export function pickVoice(voices) {
  const list = Array.isArray(voices) ? voices : [];
  const tag = (v) => String(v?.lang ?? "").replace("_", "-").toLowerCase();
  const name = (v) => String(v?.name ?? "");
  const enUS = list.filter((v) => tag(v) === "en-us");
  const natural = enUS.filter((v) => name(v).includes("Online (Natural)"));
  return (
    natural.find((v) => /\bAna\b/.test(name(v))) ??
    natural[0] ??
    enUS[0] ??
    list.find((v) => tag(v) === "en" || tag(v).startsWith("en-")) ??
    null
  );
}

const RATE = 0.9; // 아이가 알아듣기 쉽게 조금 천천히

// 브라우저가 끝 알림(onend)을 빠뜨려도 멈춰 있지 않게, 넉넉한 최대 시간이 지나면 끝난 것으로 친다.
// 너무 짧으면 멍멍이가 아직 말하는데 마이크 잠금이 풀리니 넉넉하게 잡는다.
function defaultMaxSpeakMs(text) {
  const words = String(text).trim().split(/\s+/).length;
  return Math.min(20000, Math.max(3000, 1500 + words * 600));
}

export function createTts({
  synth = globalThis.speechSynthesis,
  Utterance = globalThis.SpeechSynthesisUtterance,
  voiceWaitMs = 2000,
  maxSpeakMs = defaultMaxSpeakMs,
} = {}) {
  const can = !!synth && typeof Utterance === "function";
  let voicesReady = null;
  let current = null; // 말하는 중인 발화. Chrome 은 참조가 없는 발화를 지워 onend 를 빠뜨리기도 해서 붙잡아 둔다.

  // 목소리 목록은 늦게 온다(voiceschanged). 처음 한 번만 최대 voiceWaitMs 기다린다.
  // 기다린 뒤에도 말할 때마다 다시 고르니, Edge 의 Online 목소리가 늦게 와도 다음 말부터 쓴다.
  function voicesLoaded() {
    if (!voicesReady) {
      voicesReady = new Promise((resolve) => {
        const has = () => { try { return synth.getVoices().length > 0; } catch { return false; } };
        if (has()) return resolve();
        let timer = null;
        const done = () => {
          clearTimeout(timer);
          try { synth.removeEventListener?.("voiceschanged", onChange); } catch {}
          resolve();
        };
        const onChange = () => { if (has()) done(); };
        timer = setTimeout(done, voiceWaitMs);
        try { synth.addEventListener?.("voiceschanged", onChange); } catch { done(); }
      });
    }
    return voicesReady;
  }

  async function speak(text) {
    if (!can || !text) return;
    await voicesLoaded();
    let voices = [];
    try { voices = synth.getVoices(); } catch {}
    // 기다려도 영어 목소리가 없으면(목소리가 아예 없거나 한국어 목소리뿐) 조용히 넘어간다(자막만).
    // 그냥 speak 하면 한국어 목소리가 영어를 읽거나, 끝 알림이 오지 않아 최대 시간 내내 마이크가 잠긴다.
    const voice = pickVoice(voices);
    if (!voice) return;
    return new Promise((resolve) => {
      let guard = null;
      const entry = {
        finish() {
          if (current === entry) current = null;
          clearTimeout(guard);
          resolve();
        },
      };
      try {
        synth.cancel(); // 말하기 전에 항상: 앞 발화나 깨우기용 무음 발화가 남아 있지 않게
        const u = new Utterance(text);
        u.lang = voice.lang || "en-US";
        u.voice = voice;
        u.rate = RATE;
        u.onend = () => entry.finish();
        u.onerror = () => entry.finish();
        entry.utterance = u;
        current = entry;
        guard = setTimeout(() => entry.finish(), maxSpeakMs(text));
        synth.speak(u);
      } catch {
        entry.finish();
      }
    });
  }

  function cancel() {
    if (!can) return;
    current?.finish();
    try { synth.cancel(); } catch {}
  }

  // 첫 클릭 때 부른다: 목소리 목록을 미리 불러오고, 소리 없는 발화로 말하기 권한을 깨운다.
  function warm() {
    if (!can) return;
    voicesLoaded();
    try {
      const u = new Utterance(" ");
      u.volume = 0;
      synth.speak(u);
    } catch {}
  }

  return { speak, cancel, warm };
}
