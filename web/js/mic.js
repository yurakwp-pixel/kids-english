// 마이크: 브라우저 Web Speech(SpeechRecognition), en-US 하나. 한 번 누르면 듣고, 조용해지면 인식기가 알아서 끝난다.
// - continuous=false, interimResults=true(중간 결과는 화면 표시용), maxAlternatives=5(n-best).
// - 기기 안 인식: SpeechRecognition.available() 이 "available" 이고 processLocally 를 지원하면 그것으로 듣는다
//   (아이 목소리가 밖으로 나가지 않는다). 실패하면 다음부터는 보통 인식(브라우저 음성인식 서버)으로 돌아간다.
// - 한 번 듣기(start)마다 onResult 는 최대 한 번: 최종 n-best, 또는 아무것도 못 들었으면 [].
//   권한 거부처럼 다시 눌러도 안 되는 오류는 onResult 대신 onState("error", code) 로 알린다.
// - 이 파일은 불러올 때 브라우저 객체를 건드리지 않는다. 시험은 가짜 인식기를 넣어서 한다(tests/js/mic.test.mjs).

// 브라우저 오류 이름 → 부모용 안내 코드(main.js 가 문구로 바꾼다)
const HARD_ERRORS = {
  "not-allowed": "mic-blocked",
  "service-not-allowed": "mic-blocked",
  "audio-capture": "no-mic",
  network: "network",
};

function round2(x) {
  return typeof x === "number" && Number.isFinite(x) ? Math.round(x * 100) / 100 : 0;
}

function alternativesOf(result) {
  const alts = [];
  for (let i = 0; i < result.length; i++) {
    const t = String(result[i]?.transcript ?? "").trim();
    if (t) alts.push({ t, c: round2(result[i].confidence) });
  }
  return alts;
}

export function createMic({
  lang = "en-US",
  maxAlternatives = 5,
  onResult = () => {},
  onInterim = () => {},
  onState = () => {},
  SpeechRecognition: SR = globalThis.SpeechRecognition ?? globalThis.webkitSpeechRecognition,
} = {}) {
  const supported = typeof SR === "function";
  let localStatus = supported ? "checking" : "n/a"; // 디버그 화면용: available | downloadable | unavailable | n/a
  let useLocal = false;
  let rec = null;

  if (supported && typeof SR.available === "function") {
    Promise.resolve()
      .then(() => SR.available({ langs: [lang], processLocally: true }))
      .then((status) => {
        localStatus = String(status);
        useLocal = status === "available";
      })
      .catch(() => { localStatus = "unavailable"; });
  } else if (supported) {
    localStatus = "n/a";
  }

  function start() {
    if (!supported) return;
    if (rec) {
      // 앞 인식기가 아직 끝(onend)을 알리지 않았다. 겹쳐 시작하지 않고, 화면이 듣는 중으로 멈춰 있지 않게 idle 을 알린다.
      onState("idle");
      return;
    }
    const r = new SR();
    const local = useLocal && "processLocally" in r;
    r.lang = lang;
    r.continuous = false;
    r.interimResults = true;
    r.maxAlternatives = maxAlternatives;
    if (local) r.processLocally = true;

    let delivered = false; // 최종 결과(또는 빈 결과)를 넘겼다
    let failed = false;    // 다시 눌러도 안 되는 오류를 알렸다
    let aborted = false;   // 우리가 stop() 으로 멈췄다

    r.onresult = (event) => {
      if (delivered || failed) return;
      let interim = "";
      for (let i = event.resultIndex ?? 0; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          delivered = true;
          onResult(alternativesOf(result));
          return;
        }
        interim += result[0]?.transcript ?? "";
      }
      if (interim.trim()) onInterim(interim.trim());
    };
    r.onerror = (event) => {
      if (delivered || failed) return; // 결과 뒤에 오는 늦은 오류는 의미가 없다
      const code = event?.error;
      if (code === "aborted") {
        aborted = true;
        return;
      }
      if (code === "no-speech") return; // 끝(onend)에서 빈 결과로 넘긴다
      if (local && !HARD_ERRORS[code]) {
        useLocal = false; // 기기 안 인식이 안 된다: 다음 탭부터 보통 인식. 이번에는 빈 결과로 "다시 말해 볼까?"
        return;
      }
      failed = true;
      onState("error", HARD_ERRORS[code] ?? "mic-error");
    };
    r.onend = () => {
      if (rec === r) rec = null;
      if (!delivered && !failed && !aborted) {
        delivered = true;
        onResult([]);
      }
      onState("idle");
    };

    try {
      r.start();
    } catch {
      onState("error", "start-failed");
      return;
    }
    rec = r;
    onState("listening");
  }

  // 결과 없이 멈춘다(부모가 글자로 턴을 넣었을 때, 화면을 떠날 때).
  function stop() {
    if (!rec) return;
    try {
      rec.abort();
    } catch {
      rec = null;
    }
  }

  return {
    supported,
    get engine() {
      if (!supported) return "none";
      return useLocal ? "webspeech-local" : "webspeech";
    },
    get localStatus() {
      return localStatus;
    },
    start,
    stop,
  };
}
