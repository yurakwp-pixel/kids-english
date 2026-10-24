// 서버 부르기: 세션 시작과 턴(`POST /api/turn` → NDJSON 줄마다 onLine). DOM 을 쓰지 않는다(fetch 만).
import { readNdjson } from "./ndjson.js";

// 서버 시간 초과(8초)와 대체 대사가 있으니 보통은 그 안에 끝난다. 서버가 아예 멈췄을 때만 이 시간에 끊는다.
// main.js 는 세션 준비를 기다리는 시간까지 이 한도 안에 넣는다(withDeadline + sendTurn 의 timeoutMs).
export const TURN_TIMEOUT_MS = 15000;

/** promise 의 값을 그대로, 또는 ms 안에 끝나지 않으면 `Error("<what> timed out")` 로 reject. */
export function withDeadline(promise, ms, what = "wait") {
  let timer = null;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out`)), Math.max(0, ms));
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

const pad = (n, w = 2) => String(n).padStart(w, "0");

// 로컬 시각 `YYYY-MM-DD_HHMM`. 서버의 안전한 session_id 모양([A-Za-z0-9][A-Za-z0-9_-]*)에 맞는다.
export function makeSessionId(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}`;
}

function postJson(url, body, init = {}) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...init,
  });
}

// 새 세션을 연다. 서버가 LLM 을 미리 띄우게 하려는 것이라, 실패해도 id 는 그대로 돌려준다:
// 서버는 처음 보는 session_id 의 턴이 오면 그 세션을 알아서 연다.
export async function startSession() {
  const sessionId = makeSessionId();
  try {
    await postJson("/api/session/start", { session_id: sessionId });
  } catch {
    // 서버가 아직 안 떴거나 잠깐 끊겼다. 첫 턴에서 다시 열린다.
  }
  return sessionId;
}

// 턴 하나를 보내고 NDJSON 줄(ack, fast, reply)을 오는 대로 onLine 에 넘긴다. 모든 줄을 받으면 끝난다.
// 서버가 200 이 아니면 `Error("turn failed: <status> <error>")`, 연결이 끊기거나 너무 오래(timeoutMs) 걸리면 그 오류로 reject.
export async function sendTurn(req, onLine, { timeoutMs = TURN_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(0, timeoutMs));
  try {
    const res = await postJson("/api/turn", req, { signal: controller.signal });
    if (!res.ok) {
      let code = "";
      try { code = (await res.json()).error ?? ""; } catch {}
      throw new Error(`turn failed: ${res.status} ${code}`.trim());
    }
    for await (const obj of readNdjson(res.body)) onLine(obj);
  } finally {
    clearTimeout(timer);
  }
}
