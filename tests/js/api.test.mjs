// api.js 를 가짜 fetch 로 시험한다.
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { makeSessionId, startSession, sendTurn } from "../../web/js/api.js";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function ndjsonResponse(...objs) {
  const body = new TextEncoder().encode(objs.map((o) => JSON.stringify(o) + "\n").join(""));
  return new Response(body, { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
}

test("makeSessionId uses local time YYYY-MM-DD_HHMM", () => {
  assert.equal(makeSessionId(new Date(2026, 9, 22, 19, 5, 59)), "2026-10-22_1905");
  assert.equal(makeSessionId(new Date(2027, 0, 3, 7, 0)), "2027-01-03_0700");
});

test("startSession posts the new id and resolves it", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ ok: true }); };
  const id = await startSession();
  assert.match(id, /^\d{4}-\d{2}-\d{2}_\d{4}$/);
  assert.equal(calls[0][0], "/api/session/start");
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[0][1].body), { session_id: id });
});

test("startSession still resolves the id when the server is unreachable (turns start it implicitly)", async () => {
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  assert.match(await startSession(), /^\d{4}-\d{2}-\d{2}_\d{4}$/);
});

test("sendTurn posts the request and hands every NDJSON line to onLine in order", async () => {
  let sent;
  globalThis.fetch = async (url, init) => {
    sent = [url, init];
    return ndjsonResponse({ kind: "ack", turn_id: 1 }, { kind: "reply", turn_id: 1, say: "Hi!" });
  };
  const req = { session_id: "2026-10-22_1905", turn_id: 1, asr: { engine: "webspeech", lang: "en-US", alternatives: [] },
    hint_requested: false, scene: [] };
  const lines = [];
  await sendTurn(req, (obj) => lines.push(obj));
  assert.equal(sent[0], "/api/turn");
  assert.equal(sent[1].method, "POST");
  assert.deepEqual(JSON.parse(sent[1].body), req);
  assert.deepEqual(lines.map((l) => l.kind), ["ack", "reply"]);
});

test("sendTurn rejects with the server's error code on a non-200 answer", async () => {
  globalThis.fetch = async () => Response.json({ ok: false, error: "bad_turn_id" }, { status: 400 });
  await assert.rejects(sendTurn({}, () => {}), /400 bad_turn_id/);
});

test("sendTurn gives up after timeoutMs when the server never answers", async () => {
  globalThis.fetch = (url, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
  const started = Date.now();
  await assert.rejects(sendTurn({}, () => {}, { timeoutMs: 30 }), /abort/i);
  assert.ok(Date.now() - started < 1000);
});

test("withDeadline passes the value through, or rejects once the time is up", async () => {
  const { withDeadline, TURN_TIMEOUT_MS } = await import("../../web/js/api.js");
  assert.equal(TURN_TIMEOUT_MS, 15000);
  assert.equal(await withDeadline(Promise.resolve("2026-10-22_1905"), 50), "2026-10-22_1905");
  await assert.rejects(withDeadline(new Promise(() => {}), 20, "session start"), /session start timed out/);
  await assert.rejects(withDeadline(Promise.reject(new Error("boom")), 50), /boom/);
});
