// NDJSON(줄마다 JSON 하나) 스트림 읽기. DOM 에 기대지 않아 node 에서도 테스트한다.

// 버퍼를 줄 단위로 나눈다. 아직 줄바꿈이 오지 않은 마지막 조각은 rest 로 돌려주고, 빈 줄은 버린다.
export function splitLines(buffer) {
  const parts = buffer.split("\n");
  const rest = parts.pop();
  const lines = parts.map((line) => line.replace(/\r$/, "")).filter((line) => line.trim() !== "");
  return { lines, rest };
}

// fetch 응답의 body(ReadableStream<Uint8Array>)에서 줄마다 JSON.parse 한 객체를 차례로 내보낸다.
// 깨진 줄은 건너뛰지 않고 SyntaxError 로 던진다. 마지막 줄에 줄바꿈이 없어도 읽는다.
// 끝까지 읽기 전에 멈추면(받는 쪽이 break 하거나 깨진 줄로 던질 때) 스트림을 cancel 해서 연결을 놓아 준다.
export async function* readNdjson(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let ended = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        ended = true;
        break;
      }
      // stream:true — 한글처럼 여러 바이트인 글자가 청크 경계에서 잘려도 이어 붙여 읽는다.
      buffer += decoder.decode(value, { stream: true });
      const { lines, rest } = splitLines(buffer);
      buffer = rest;
      for (const line of lines) yield JSON.parse(line);
    }
    // 스트림이 끝났다: 줄바꿈 없이 끝난 마지막 조각도 한 줄로 친다.
    buffer += decoder.decode();
    for (const line of splitLines(buffer + "\n").lines) yield JSON.parse(line);
  } finally {
    if (!ended) {
      try {
        await reader.cancel();
      } catch {
        // 이미 끊긴 스트림이면 cancel 도 실패할 수 있다. 원래 오류(또는 정상 종료)를 그대로 둔다.
      }
    }
    reader.releaseLock();
  }
}
