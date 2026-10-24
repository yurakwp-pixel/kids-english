// 부모용 디버그 패널(`?debug=1` 일 때만 main.js 가 만든다). 아이 화면에는 입력창이 없다.
// - 글자 입력: 쉼표로 나눈 n-best("hello chomp, hello champ")를 마이크 대신 턴으로 넣는다. 빈 칸이면 빈 턴.
// - 보이는 것: 화면 상태, 마이크 엔진(기기 안 인식 여부), 받은 NDJSON 줄과 도착 시각(ms), 서버 지연, 마지막 요청·응답 JSON.
// 값은 모두 textContent 로 넣는다(innerHTML 없음).

// "a, b, c" → [{t:"a",c:1}, {t:"b",c:0.9}, {t:"c",c:0.8}] (앞일수록 확신이 크다고 친다)
export function parseNbest(text) {
  return String(text)
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 5)
    .map((t, i) => ({ t, c: Math.round((1 - i * 0.1) * 100) / 100 }));
}

function make(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

export function createDebugPanel(host, { onSubmit, micInfo }) {
  const input = make("input", { type: "text", name: "said", placeholder: "hello chomp, hello champ", autocomplete: "off" });
  const form = make("form", {}, input, make("button", { type: "submit", textContent: "Send" }));
  const note = make("p", { className: "dbg-note" });
  const fact = {};
  const facts = make("dl");
  for (const [key, label] of [
    ["mode", "mode"], ["turn", "turn"], ["engine", "mic engine"], ["local", "on-device"],
    ["reqEngine", "request engine"], ["latency", "latency"],
  ]) {
    fact[key] = make("dd", { textContent: "-" });
    facts.append(make("dt", { textContent: label }), fact[key]);
  }
  const lines = make("ol");
  const request = make("pre", { textContent: "-" });
  const response = make("pre", { textContent: "-" });

  const panel = make(
    "aside",
    { className: "debug-panel", ariaLabel: "debug" },
    make("h2", { textContent: "debug (parent only)" }),
    form,
    make("p", { className: "dbg-hint", textContent: "n-best: comma-separated. Empty = empty turn." }),
    note,
    facts,
    make("h3", { textContent: "lines (ms after send)" }),
    lines,
    make("h3", { textContent: "last request" }),
    request,
    make("h3", { textContent: "last response" }),
    response,
  );
  host.append(panel);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const accepted = onSubmit(parseNbest(input.value));
    note.textContent = accepted ? "" : "ignored: Chomp is busy (mic locked)";
    if (accepted) input.select();
  });
  input.focus();

  function refreshMic() {
    const m = micInfo();
    fact.engine.textContent = m.supported ? m.engine : "none (no SpeechRecognition)";
    fact.local.textContent = m.localStatus;
  }
  refreshMic();
  setInterval(refreshMic, 1000); // 기기 안 인식 확인(available)은 늦게 끝나서 상태가 안 바뀌어도 다시 읽는다

  return {
    state(s) {
      fact.mode.textContent = s.mode;
      fact.turn.textContent = String(s.turnId);
      refreshMic();
    },
    turnStarted(req) {
      lines.replaceChildren();
      fact.reqEngine.textContent = req.asr.engine;
      fact.latency.textContent = "…";
      request.textContent = JSON.stringify(req, null, 2);
      response.textContent = "…";
    },
    line(obj, ms) {
      let text = `${ms} ms  ${obj.kind}`;
      if (obj.kind === "reply") {
        const lat = obj.latency ?? {};
        text += `  source=${obj.source}  llm_ms=${lat.llm_ms}  total_ms=${lat.total_ms}`;
        fact.latency.textContent = `client ${ms} ms · server total ${lat.total_ms} ms · llm ${lat.llm_ms} ms`;
        response.textContent = JSON.stringify(obj, null, 2);
      }
      lines.append(make("li", { textContent: text }));
    },
    failed(error, ms) {
      lines.append(make("li", { textContent: `${ms} ms  failed: ${error?.message ?? error}` }));
      fact.latency.textContent = `failed after ${ms} ms`;
      response.textContent = String(error?.message ?? error);
    },
  };
}
