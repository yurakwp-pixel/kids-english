import { test } from "node:test";
import assert from "node:assert/strict";
import { splitLines, readNdjson } from "../../web/js/ndjson.js";

test("splitLines keeps partial tail", () => {
  assert.deepEqual(splitLines('{"a":1}\n{"b"'), { lines: ['{"a":1}'], rest: '{"b"' });
});

test("splitLines ignores blank lines", () => {
  assert.deepEqual(splitLines('\n{"a":1}\n\n'), { lines: ['{"a":1}'], rest: "" });
});

test("splitLines handles CRLF line ends", () => {
  assert.deepEqual(splitLines('{"a":1}\r\n{"b":2}\r\n'), { lines: ['{"a":1}', '{"b":2}'], rest: "" });
});

function streamOf(...chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function collect(stream) {
  const out = [];
  for await (const obj of readNdjson(stream)) out.push(obj);
  return out;
}

test("readNdjson yields one parsed object per line across chunk boundaries", async () => {
  const enc = new TextEncoder();
  const got = await collect(streamOf(enc.encode('{"a":1}\n{"b"'), enc.encode(':2}\n'), enc.encode('{"c":3}\n')));
  assert.deepEqual(got, [{ a: 1 }, { b: 2 }, { c: 3 }]);
});

test("readNdjson keeps multi-byte characters split between chunks", async () => {
  const bytes = new TextEncoder().encode('{"say":"한글"}\n');
  const got = await collect(streamOf(bytes.slice(0, 10), bytes.slice(10)));
  assert.deepEqual(got, [{ say: "한글" }]);
});

test("readNdjson parses a final line that has no trailing newline", async () => {
  const got = await collect(streamOf(new TextEncoder().encode('{"a":1}\n{"b":2}')));
  assert.deepEqual(got, [{ a: 1 }, { b: 2 }]);
});

test("readNdjson rejects on a malformed line instead of skipping it", async () => {
  await assert.rejects(collect(streamOf(new TextEncoder().encode("not json\n"))), SyntaxError);
});

// 아직 닫히지 않은 스트림: 받는 쪽이 일찍 멈추면 cancel 이 불려야 한다(연결을 붙잡고 있지 않게).
function openStreamOf(text, cancelled) {
  return new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode(text)); },
    cancel(reason) { cancelled.push(reason); },
  });
}

test("readNdjson cancels the stream when the consumer stops early", async () => {
  const cancelled = [];
  const stream = openStreamOf('{"a":1}\n{"b":2}\n', cancelled);
  for await (const obj of readNdjson(stream)) {
    assert.deepEqual(obj, { a: 1 });
    break;
  }
  assert.equal(cancelled.length, 1);
  assert.equal(stream.locked, false);
});

test("readNdjson cancels the stream when a line fails to parse", async () => {
  const cancelled = [];
  const stream = openStreamOf('{"a":1}\nnot json\n', cancelled);
  await assert.rejects(collect(stream), SyntaxError);
  assert.equal(cancelled.length, 1);
  assert.equal(stream.locked, false);
});

test("readNdjson does not cancel a stream that ended normally", async () => {
  const cancelled = [];
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('{"a":1}\n')); controller.close(); },
    cancel(reason) { cancelled.push(reason); },
  });
  assert.deepEqual(await collect(stream), [{ a: 1 }]);
  assert.equal(cancelled.length, 0);
});
