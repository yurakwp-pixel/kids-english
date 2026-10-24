// 화면 상태 기계. DOM 을 모르는 순수 함수라 node 로 시험한다(tests/js/ui_state.test.mjs).
//
//   idle ──TAP_MIC──▶ listening ──MIC_RESULT(최종)──▶ thinking ──LINE_REPLY──▶ speaking ──TTS_END──▶ idle
//                       │  MIC_END / ERROR                 (LINE_ACK, LINE_FAST 는 thinking 그대로)
//                       ▼
//                      idle
//
// 마이크 잠금(Review Focus 2): thinking·speaking 중에는 TAP_MIC, 새 MIC_RESULT, MIC_END, ERROR 를 모두 무시한다.
// 그래서 답을 기다리거나 멍멍이가 말하는 동안 아이가 다시 눌러도 턴이 겹치지 않고, 자기 목소리를 듣지도 않는다.
// 바뀐 것이 없으면 같은 객체를 돌려준다(main.js 는 `next !== prev` 로 다시 그릴지 정한다).

export const initialUiState = Object.freeze({
  mode: "idle",   // idle | listening | thinking | speaking
  turnId: 0,      // 마지막으로 시작한 턴 번호. 최종 인식 결과가 올 때마다 1씩 는다.
  heard: "",      // 들은 말(중간 결과 또는 최종 1순위)
  say: "",        // 멍멍이 대사(영어 자막)
  face: "happy",  // 멍멍이 표정
  acked: false,   // 이번 턴의 ack 줄을 받았는지
  notice: null,   // 부모에게 보여 줄 안내 코드(mic-blocked, no-mic, network ...). 다음 TAP_MIC 에서 지운다.
});

const LOCKED = new Set(["thinking", "speaking"]);

// 줄에 turn_id 가 있으면 지금 턴의 것만 받는다. 어느 한쪽이 번호를 모르면 받는다.
function sameTurn(state, event) {
  return event.turn_id == null || state.turnId == null || event.turn_id === state.turnId;
}

function firstText(alts) {
  const first = Array.isArray(alts) ? alts[0] : null;
  return first && typeof first.t === "string" ? first.t : "";
}

export function uiReducer(state, event) {
  const s = state ?? initialUiState;
  if (!event || typeof event.type !== "string") return s;

  switch (event.type) {
    case "TAP_MIC":
      // listening 중 두 번째 탭도 무시한다: 조용해지면 인식기가 알아서 끝난다(말을 도중에 끊지 않게).
      if (s.mode !== "idle") return s;
      return { ...s, mode: "listening", heard: "", notice: null };

    case "MIC_RESULT": {
      if (event.final === false) {
        if (s.mode !== "listening") return s;
        return { ...s, heard: firstText(event.alts) };
      }
      // 최종 결과: listening(마이크) 또는 idle(부모용 글자 입력)에서만 새 턴을 연다. 빈 결과도 턴이다
      // (서버가 LLM 없이 "Say it again!" 으로 답한다, Review Focus 3).
      if (LOCKED.has(s.mode)) return s;
      return { ...s, mode: "thinking", turnId: (s.turnId ?? 0) + 1, heard: firstText(event.alts), acked: false, notice: null };
    }

    case "MIC_END":
      // 결과 없이 인식이 끝났다(중단 등). 이미 턴이 시작됐으면 무시한다.
      return s.mode === "listening" ? { ...s, mode: "idle" } : s;

    case "LINE_ACK":
    case "LINE_FAST": // M2: fast 줄의 ops 는 main.js 가 장면에 바로 적용한다. 상태는 thinking 그대로.
      if (s.mode !== "thinking" || !sameTurn(s, event)) return s;
      return s.acked ? s : { ...s, acked: true };

    case "LINE_REPLY":
      if (s.mode !== "thinking" || !sameTurn(s, event)) return s;
      return {
        ...s,
        mode: "speaking",
        say: typeof event.say === "string" ? event.say : "",
        face: typeof event.face === "string" && event.face ? event.face : "happy",
      };

    case "TTS_END":
      if (s.mode !== "speaking" || !sameTurn(s, event)) return s;
      return { ...s, mode: "idle" };

    case "ERROR":
      // 마이크 쪽 오류(권한 거부 등). 턴이 진행 중이면 무시한다: 여기서 idle 로 돌아가면 잠금이 풀린다.
      if (LOCKED.has(s.mode)) return s;
      return { ...s, mode: "idle", notice: typeof event.code === "string" ? event.code : "error" };

    default:
      return s;
  }
}
