// 화면 연결: 마이크 → 서버 턴 → 놀이판이 바뀌고 → 멍멍이 대사(자막 + 목소리). 상태 전이는 ui_state.js 의 순수 리듀서가 정하고,
// 여기서는 바뀐 상태를 그리고(renderUi) 그 전이에 딸린 일(마이크 켜기, 턴 보내기, 말하기)을 한다(runEffects).
//
// 한 턴: 🎤 탭(TAP_MIC) → 듣기 → 최종 인식(MIC_RESULT) → POST /api/turn(장면은 toWire)
//        → 1줄 fast: 그 ops 를 바로 장면에(director → render, "퐁" 효과음) / 1줄 ack: 그대로 생각 중
//        → 2줄 reply: 그 ops 와 표정(mood)도 장면에, 1줄의 움직임이 보인 뒤(최대 1.6초) say 를 말하기(TTS, 입 여닫기)
//        → 말이 끝나면(TTS_END) 다시 🎤.
// 생각·말하는 중에는 리듀서가 탭을 무시한다(마이크 잠금). 서버에 닿지 못해도 멍멍이는 준비된 한마디로 답한다.
// 멈춤 방지: 세션 준비 기다리기까지 15초 한도 안(TURN_TIMEOUT_MS), 듣기가 12초 넘으면 빈 결과로 턴("Say it again!").
import { uiReducer, initialUiState } from "./ui_state.js";
import { createMic } from "./mic.js";
import { createTts } from "./tts.js";
import { startSession, sendTurn, withDeadline, TURN_TIMEOUT_MS } from "./api.js";
import { toWire } from "./scene/model.js";
import { layout } from "./scene/layout.js";
import { render as renderBoard, setChompMouth } from "./scene/render.js";
import { createDirector } from "./scene/director.js";
import { createDebugPanel } from "./debug.js";
import { startShowing, endTurn } from "./turn_end.js";

const GREETING = "Hi! I'm Chomp! Let's talk!";
const GREETING_HINT = "인사해 보자 → Hello, Chomp!"; // 첫 화면의 (?): 아직 답이 없어도 처음 할 말을 알려 준다
const CLIENT_FALLBACK = "Hmm! Let's try that again!"; // 서버의 대체 대사와 같은 말(server/canned.py)
const MOUTH_FLAP_MS = 200;
const LISTEN_WATCHDOG_MS = 12000; // 인식기가 끝을 알리지 않아도 듣는 중으로 멈춰 있지 않게
const SAY_AFTER_SCENE_MAX_MS = 1600; // 장면이 먼저 바뀌고 말한다. 부화(1.1초)+물건(0.45초)까지 기다려도 이만큼이면 말한다.

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
  board: $("board"),
  say: $("say"),
  heard: $("heard"),
  mic: $("mic"),
  ask: $("ask"),
  hint: $("hint"),
  unsupported: $("unsupported"),
  notice: $("notice"),
};

const debugMode = new URLSearchParams(location.search).get("debug") === "1";
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

let state = { ...initialUiState, hint: GREETING_HINT };

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

// ---------------------------------------------------------------- 놀이판

// 카탈로그(서버와 같은 파일)가 오기 전에도 첫 화면(둥지 속 알)은 그린다. 턴은 카탈로그가 온 뒤에 보낸다.
let catalog = {};
const director = createDirector({
  onChange: (scene, ops, source) => {
    drawBoard(scene, ops);
    debug?.ops(source, ops);
  },
});
const catalogReady = fetch("/shared/catalog.json")
  .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`catalog ${res.status}`))))
  .then((loaded) => {
    catalog = loaded;
    director.setCatalog(loaded);
    drawBoard(director.scene, []);
    return loaded;
  });
catalogReady.catch(() => {}); // 실패는 턴에서 알린다(카탈로그 없이 보내지 않는다)

function drawBoard(scene, ops = []) {
  renderBoard(el.board, scene, layout(scene, catalog), catalog, { ops });
}

// mood {actor, face} → 장면의 표정. 표정도 장면에 있어야 다음 턴의 toWire 가 서버에 알린다.
function moodOps(mood) {
  return mood && typeof mood.actor === "string" && typeof mood.face === "string"
    ? [{ op: "set", target: mood.actor, face: mood.face }]
    : [];
}

// ---------------------------------------------------------------- 상태

// 상태가 바뀌었으면 true(디버그 패널이 "무시됨"을 보여 줄 때, 턴이 대체 대사를 낼지 정할 때 쓴다).
function dispatch(event) {
  const prev = state;
  state = uiReducer(prev, event);
  if (state === prev) return false;
  try {
    renderUi(prev, state);
  } finally {
    runEffects(prev, state, event); // 그리기가 throw 해도 전이에 딸린 일(턴 보내기, 말하기 → TTS_END)은 한다: 상태가 멈추지 않게
  }
  return true;
}

let listenTimer = null;

function runEffects(prev, next, event) {
  if (prev.mode === "idle" && next.mode === "listening") {
    blip();
    mic.start();
    // 인식기가 끝(onend)을 빠뜨려도 듣는 중으로 멈추지 않는다: 빈 결과로 턴을 열면 서버가 "Say it again!" 류로 답한다.
    clearTimeout(listenTimer);
    listenTimer = setTimeout(() => {
      if (state.mode === "listening") dispatch({ type: "MIC_RESULT", final: true, alts: [], engine: mic.engine });
    }, LISTEN_WATCHDOG_MS);
  }
  if (prev.mode === "listening" && next.mode !== "listening") clearTimeout(listenTimer);
  if (next.mode === "thinking" && prev.mode !== "thinking") {
    mic.stop(); // 이미 결과를 넘긴 인식기면 아무 일도 없다. 글자 입력이나 감시 시간으로 턴이 열렸으면 듣기를 끈다.
    runTurn(next.turnId, event.alts ?? [], event.engine ?? mic.engine, next.hintRequested === true);
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

// ---------------------------------------------------------------- 화면(말풍선, 버튼, 힌트)

function renderUi(prev, s) {
  el.app.dataset.mode = s.mode;
  el.mic.setAttribute("aria-disabled", String(s.mode === "thinking" || s.mode === "speaking"));
  el.ask.setAttribute("aria-disabled", String(!s.hint || s.mode === "thinking"));
  el.say.textContent = s.mode === "thinking" ? "…" : s.say || GREETING;
  el.heard.textContent = s.heard ? `“${s.heard}”` : "";
  el.heard.hidden = !s.heard || s.mode === "idle";
  el.hint.textContent = s.hintShown ? s.hint : "";
  el.hint.hidden = !(s.hintShown && s.hint);
  el.notice.textContent = s.notice ? NOTICES[s.notice] ?? NOTICES["mic-error"] : "";
  el.notice.hidden = !s.notice;
  debug?.state(s);
}

let mouthTimer = null;
let mouthOpen = true;

function startMouth() {
  stopMouth();
  if (reducedMotion.matches) return; // 동작 줄이기: 입은 벌린 채 그대로
  mouthTimer = setInterval(() => {
    mouthOpen = !mouthOpen;
    setChompMouth(el.board, mouthOpen);
  }, MOUTH_FLAP_MS);
}

function stopMouth() {
  clearInterval(mouthTimer);
  mouthTimer = null;
  mouthOpen = true;
  setChompMouth(el.board, true);
}

// ---------------------------------------------------------------- 턴

function chompFace(mood) {
  return mood && mood.actor === "chomp" && typeof mood.face === "string" ? mood.face : "happy";
}

const sameTurn = (line, turnId) => line.turn_id == null || line.turn_id === turnId;

async function runTurn(turnId, alternatives, engine, hintRequested) {
  const started = performance.now();
  const ms = () => Math.round(performance.now() - started);
  let replyLine = null;
  let replyShown = null; // startShowing: 장면이 먼저 움직이고 나서 LINE_REPLY 를 보낸다. reply 줄이 안 오면 null
  try {
    // 세션·카탈로그 준비도 15초 한도 안에서 기다린다(서버가 멈춰 있어도 생각 중에 갇히지 않는다).
    const [sessionId] = await withDeadline(Promise.all([sessionReady, catalogReady]), TURN_TIMEOUT_MS, "session start");
    const req = {
      session_id: sessionId,
      turn_id: turnId,
      asr: { engine, lang: "en-US", alternatives },
      hint_requested: hintRequested,
      scene: toWire(director.flush()), // 남은 연출과 쉬는 자세까지 적용한 최종 장면
    };
    debug?.turnStarted(req);
    await sendTurn(req, (line) => {
      debug?.line(line, ms());
      if (!sameTurn(line, turnId)) return;
      if (line.kind === "ack") {
        dispatch({ type: "LINE_ACK", turn_id: line.turn_id });
      } else if (line.kind === "fast") {
        dispatch({ type: "LINE_FAST", turn_id: line.turn_id, ops: line.ops });
        if (line.sfx === "pop") pop();
        director.play(line.ops, "fast");
      } else if (line.kind === "reply" && !replyLine) {
        replyLine = line;
        director.play([...(Array.isArray(line.ops) ? line.ops : []), ...moodOps(line.mood)], "reply");
        // dispatch 의 결과(true = 리듀서가 이 답을 받았다)가 endTurn 으로 간다. 받지 않았으면(다른 턴, 다른 상태) 대체 대사.
        replyShown = startShowing(
          () => director.whenIdle(SAY_AFTER_SCENE_MAX_MS),
          () => dispatch({
            type: "LINE_REPLY", turn_id: line.turn_id, say: line.say, face: chompFace(line.mood), hint: line.korean_hint,
          }),
        );
      }
    }, { timeoutMs: Math.max(1000, TURN_TIMEOUT_MS - ms()) });
  } catch (error) {
    debug?.failed(error, ms());
  }
  // 서버가 꺼졌거나 줄이 깨졌거나, 답을 보이다가(연출 기다리기, dispatch, 그리기) 무엇이 throw 해도 놀이는 멈추지 않는다:
  // endTurn 은 reject 하지 않고, 답이 보이지 않았으면 대체 대사를 낸다(생각 중에 갇히지 않는다).
  await endTurn(
    replyShown,
    () => dispatch({ type: "LINE_REPLY", turn_id: turnId, say: CLIENT_FALLBACK, face: "happy", hint: "" }),
    (error) => debug?.failed(error, ms()),
  );
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

// 작고 짧은 소리(Web Audio 로 만든다, 파일 없음). 크기는 0.06 이하.
function tone({ type = "sine", from, to, peak, seconds }) {
  if (!audio) return;
  try {
    const t = audio.currentTime;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(from, t);
    osc.frequency.exponentialRampToValueAtTime(to, t + seconds * 0.75);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(peak, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + seconds);
    osc.connect(gain).connect(audio.destination);
    osc.start(t);
    osc.stop(t + seconds + 0.02);
  } catch {}
}

const blip = () => tone({ from: 660, to: 990, peak: 0.06, seconds: 0.16 }); // 듣기 시작 "삐롱"
const pop = () => tone({ type: "triangle", from: 880, to: 330, peak: 0.05, seconds: 0.12 }); // 장면이 바뀌는 "퐁"

// ---------------------------------------------------------------- 시작

// pointerup: 마우스와 터치 모두 사용자 활성화가 인정되는 시점이다(터치의 pointerdown 은 아니다).
function onTap(button, handler) {
  button.addEventListener("pointerup", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    wake();
    handler();
  });
  button.addEventListener("contextmenu", (event) => event.preventDefault()); // 길게 누르기 메뉴 막기
}
onTap(el.mic, () => dispatch({ type: "TAP_MIC" }));
onTap(el.ask, () => dispatch({ type: "TAP_HINT" })); // 서버를 부르지 않는다: 직전 답의 korean_hint 를 보일 뿐
document.addEventListener("pointerup", wake, { once: true });

window.addEventListener("pagehide", () => {
  mic.stop();
  tts.cancel();
});

if (!mic.supported) {
  el.mic.hidden = true;
  el.unsupported.hidden = false;
}

drawBoard(director.scene, []);
renderUi(null, state);
