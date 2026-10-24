// 화면 연결: 마이크 → 서버 턴 → 멍멍이 대사(자막 + 목소리). 상태 전이는 ui_state.js 의 순수 리듀서가 정하고,
// 여기서는 바뀐 상태를 그리고(render) 그 전이에 딸린 일(마이크 켜기, 턴 보내기, 말하기)을 한다(runEffects).
//
// 한 턴: 🎤 탭(TAP_MIC) → 듣기 → 최종 인식(MIC_RESULT) → POST /api/turn → ack 줄(LINE_ACK) → reply 줄(LINE_REPLY)
//        → 말하기(입 0.2초 간격으로 열고 닫기) → 말이 끝나면(TTS_END) 다시 🎤.
// 생각·말하는 중에는 리듀서가 탭을 무시한다(마이크 잠금). 서버에 닿지 못해도 멍멍이는 준비된 한마디로 답한다.
import { uiReducer, initialUiState } from "./ui_state.js";
import { createMic } from "./mic.js";
import { createTts } from "./tts.js";
import { startSession, sendTurn } from "./api.js";
import { chompSvg } from "./scene/sprites.js";
import { createDebugPanel } from "./debug.js";

const GREETING = "Hi! I'm Chomp! Let's talk!";
const CLIENT_FALLBACK = "Hmm! Let's try that again!"; // 서버의 대체 대사와 같은 말(server/canned.py)
const MOUTH_FLAP_MS = 200;

// 부모에게 보이는 안내(한국어). 멍멍이 대사가 아니라서 말풍선 밖에 둔다.
const NOTICES = {
  "mic-blocked": "🎤 마이크를 허락해 주세요 (주소창의 🔒 → 마이크 허용)",
  "no-mic": "🎤 마이크를 찾지 못했어요",
  network: "🌐 인터넷 연결을 확인해 주세요",
  "start-failed": "🎤 잠깐 뒤에 다시 눌러 주세요",
  "mic-error": "🎤 잠깐 뒤에 다시 눌러 주세요",
};

const $ = (id) => document.getElementById(id);
const el = {
  app: $("app"),
  say: $("say"),
  heard: $("heard"),
  mic: $("mic"),
  unsupported: $("unsupported"),
  notice: $("notice"),
  chompArt: $("chomp-art"),
};

const debugMode = new URLSearchParams(location.search).get("debug") === "1";
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

let state = initialUiState;

const tts = createTts();
const mic = createMic({
  lang: "en-US",
  maxAlternatives: 5,
  onResult: (alts) => dispatch({ type: "MIC_RESULT", final: true, alts, engine: mic.engine }),
  onInterim: (text) => dispatch({ type: "MIC_RESULT", final: false, alts: [{ t: text, c: 0 }] }),
  onState: (s, code) => {
    if (s === "idle") dispatch({ type: "MIC_END" });
    else if (s === "error") dispatch({ type: "ERROR", code });
  },
});

// 화면을 열자마자 세션을 연다: 서버가 LLM 을 미리 띄워 첫 턴이 빨라진다.
const sessionReady = startSession();

const debug = debugMode
  ? createDebugPanel(el.app, {
      onSubmit: (alts) => {
        wake();
        return dispatch({ type: "MIC_RESULT", final: true, alts, engine: "debug-text" });
      },
      micInfo: () => ({ supported: mic.supported, engine: mic.engine, localStatus: mic.localStatus }),
    })
  : null;
if (debugMode) el.app.classList.add("debug");

// ---------------------------------------------------------------- 상태

// 상태가 바뀌었으면 true(디버그 패널이 "무시됨"을 보여 줄 때 쓴다).
function dispatch(event) {
  const prev = state;
  state = uiReducer(prev, event);
  if (state === prev) return false;
  render(prev, state);
  runEffects(prev, state, event);
  return true;
}

function runEffects(prev, next, event) {
  if (prev.mode === "idle" && next.mode === "listening") {
    blip();
    mic.start();
  }
  if (next.mode === "thinking" && prev.mode !== "thinking") {
    mic.stop(); // 이미 결과를 넘긴 인식기면 아무 일도 없다. 글자 입력으로 턴이 열렸으면 듣기를 끈다.
    runTurn(next.turnId, event.alts ?? [], event.engine ?? mic.engine);
  }
  if (next.mode === "speaking" && prev.mode !== "speaking") {
    const turnId = next.turnId;
    startMouth();
    tts.speak(next.say)
      .catch(() => {})
      .then(() => dispatch({ type: "TTS_END", turn_id: turnId }));
  }
  if (prev.mode === "speaking" && next.mode !== "speaking") stopMouth();
}

// ---------------------------------------------------------------- 그리기

let mouthOpen = true;
let drawnChomp = "";

function drawChomp() {
  const svg = chompSvg({ mouthOpen, face: state.face });
  if (svg !== drawnChomp) {
    el.chompArt.innerHTML = svg; // 우리가 만든 문자열만 넣는다(표정 이름은 sprites.js 의 표에서 고른다)
    drawnChomp = svg;
  }
}

function render(prev, s) {
  el.app.dataset.mode = s.mode;
  el.mic.setAttribute("aria-disabled", String(s.mode === "thinking" || s.mode === "speaking"));
  el.say.textContent = s.mode === "thinking" ? "…" : s.say || GREETING;
  el.heard.textContent = s.heard ? `“${s.heard}”` : "";
  el.heard.hidden = !s.heard || s.mode === "idle";
  el.notice.textContent = s.notice ? NOTICES[s.notice] ?? NOTICES["mic-error"] : "";
  el.notice.hidden = !s.notice;
  if (s.face !== prev?.face) drawChomp();
  debug?.state(s);
}

let mouthTimer = null;

function startMouth() {
  stopMouth();
  if (reducedMotion.matches) return; // 동작 줄이기: 입은 벌린 채 그대로
  mouthTimer = setInterval(() => {
    mouthOpen = !mouthOpen;
    drawChomp();
  }, MOUTH_FLAP_MS);
}

function stopMouth() {
  clearInterval(mouthTimer);
  mouthTimer = null;
  mouthOpen = true;
  drawChomp();
}

// ---------------------------------------------------------------- 턴

function chompFace(mood) {
  return mood && mood.actor === "chomp" && typeof mood.face === "string" ? mood.face : "happy";
}

async function runTurn(turnId, alternatives, engine) {
  const sessionId = await sessionReady;
  const req = {
    session_id: sessionId,
    turn_id: turnId,
    asr: { engine, lang: "en-US", alternatives },
    hint_requested: false,
    scene: [],
  };
  debug?.turnStarted(req);
  const sent = performance.now();
  const ms = () => Math.round(performance.now() - sent);
  let replied = false;
  try {
    await sendTurn(req, (line) => {
      debug?.line(line, ms());
      if (line.kind === "ack") dispatch({ type: "LINE_ACK", turn_id: line.turn_id });
      else if (line.kind === "fast") dispatch({ type: "LINE_FAST", turn_id: line.turn_id, ops: line.ops });
      else if (line.kind === "reply" && !replied) {
        replied = true;
        dispatch({ type: "LINE_REPLY", turn_id: line.turn_id, say: line.say, face: chompFace(line.mood) });
      }
    });
  } catch (error) {
    debug?.failed(error, ms());
  }
  // 서버가 꺼졌거나 줄이 깨졌어도 놀이는 멈추지 않는다.
  if (!replied) dispatch({ type: "LINE_REPLY", turn_id: turnId, say: CLIENT_FALLBACK, face: "happy" });
}

// ---------------------------------------------------------------- 소리 깨우기와 효과음

let audio = null;
let woke = false;

// 브라우저 규칙: 소리(AudioContext)와 말하기(speechSynthesis)는 사용자가 한 번 눌러야 켜진다. 첫 클릭에서 깨운다.
// 사용자 활성화가 없는 호출(스크립트로 넣은 디버그 턴 등)에서는 깨우지 않는다: 경고만 남고 켜지지 않는다.
function wake() {
  if (navigator.userActivation && !navigator.userActivation.isActive) return;
  if (!woke) {
    woke = true;
    const AC = globalThis.AudioContext ?? globalThis.webkitAudioContext;
    if (AC) {
      try { audio = new AC(); } catch { audio = null; }
    }
    tts.warm();
  }
  if (audio?.state === "suspended") audio.resume().catch(() => {});
}

// 듣기 시작을 알리는 작고 짧은 "삐롱"(Web Audio 로 만든다, 파일 없음).
function blip() {
  if (!audio) return;
  try {
    const t = audio.currentTime;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(660, t);
    osc.frequency.exponentialRampToValueAtTime(990, t + 0.12);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.06, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
    osc.connect(gain).connect(audio.destination);
    osc.start(t);
    osc.stop(t + 0.18);
  } catch {}
}

// ---------------------------------------------------------------- 시작

// pointerup: 마우스와 터치 모두 사용자 활성화가 인정되는 시점이다(터치의 pointerdown 은 아니다).
el.mic.addEventListener("pointerup", (event) => {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  wake();
  dispatch({ type: "TAP_MIC" });
});
el.mic.addEventListener("contextmenu", (event) => event.preventDefault()); // 길게 누르기 메뉴 막기
document.addEventListener("pointerup", wake, { once: true });

window.addEventListener("pagehide", () => {
  mic.stop();
  tts.cancel();
});

if (!mic.supported) {
  el.mic.hidden = true;
  el.unsupported.hidden = false;
}

render(null, state);
