# ADR-0002: 표준 라이브러리 HTTP 서버와 asyncio LLM 액터 스레드

**Status:** Accepted
**Date:** 2026-10-25 (방향은 2026-10-22 spec 5.3에서 정했고, Task 4·6에서 구현했다)
**Deciders:** 부모(개발자)

줄 번호는 M2 끝(커밋 `37952d8`)의 실제 줄로 다시 맞췄다(Task 16). M2 최종 리뷰 수정 뒤(커밋 `fea7dc3`) `server/turn.py`, `web/js/main.js`의 줄을 한 번 더 맞췄다. `server/llm/claude_agent.py`와 `server/app.py`는 M1 뒤로 바뀌지 않았다.

> 한 줄로: HTTP는 표준 라이브러리 `ThreadingHTTPServer`가 보통 함수(동기)로 처리한다. Claude SDK는 세션마다 전용 스레드 하나가 자기 asyncio 이벤트 루프 안에서 쥔다. 둘 사이는 `run_coroutine_threadsafe`와 큐 하나로 잇는다.

## Context

- **스택 제약(spec 3절):** Python 표준 라이브러리 HTTP 서버 + 순수 HTML/CSS/JS. 프레임워크와 빌드 도구는 쓰지 않는다. 런타임 의존성은 `claude-agent-sdk==0.2.172` 하나다.
- **SDK는 비동기(async)다.** `ClaudeSDKClient`는 `async with`로 열고 `await client.query()`, `async for ... in client.receive_response()`로 쓴다.
- **SDK의 async 컨텍스트 제약:** SDK 문서(`ClaudeSDKClient` docstring의 Caveat)는 이렇게 말한다.
  - 한 클라이언트를 다른 async 컨텍스트(다른 asyncio task group 등)에서 쓸 수 없다.
  - 클라이언트는 connect부터 disconnect까지 메시지를 읽는 anyio task group 하나를 계속 살려 둔다.
  - 그래서 **연결, 질의, 종료를 모두 연결한 그 async 컨텍스트 안에서** 해야 한다.
- **[ADR-0001](0001-local-claude-via-agent-sdk.md)이 정한 것:** 클라이언트 하나를 세션 동안 살려 둔다. 그러려면 그 async 컨텍스트도 세션 동안 살아 있어야 한다.
- **아이는 한 명이다.** 턴이 동시에 들어올 일은 사실상 없다. 그래도 아이가 연달아 누르면 겹치지 않아야 한다(Review Focus 2).
- **스트리밍이 필요하다.** `/api/turn`은 ack 줄을 LLM을 부르기 **전에** 보내고, 나중에 reply 줄을 보낸다. 핸들러가 줄마다 쓰고 flush할 수 있어야 한다.

## Decision

줄 번호만 적은 것(`:216` 같은)은 `server/llm/claude_agent.py`의 줄이다.

1. **HTTP:** `http.server.ThreadingHTTPServer`(`server/app.py:221`). 요청마다 스레드 하나가 보통 함수로 처리한다. `/api/turn`은 `TurnOrchestrator.handle`이 내는 줄을 하나씩 `write` + `flush`한다(`server/app.py:161`–`187`). HTTP/1.0이라 본문 길이 대신 연결을 닫아 끝을 알린다.
2. **LLM 액터:** 세션마다 `_Session` 하나(`server/llm/claude_agent.py:208`).
   - 데몬 스레드 하나가 `asyncio.new_event_loop()`로 자기 루프를 만들고 `run_until_complete(self._actor())`를 돌린다(`:216`, `:229`–`232`).
   - 액터 코루틴 하나가 `async with ClaudeSDKClient(options)`를 세션 내내 쥐고(`:247`), 받은편지함 `asyncio.Queue`에서 턴을 하나씩 꺼내 처리한다(`:251`–`259`).
3. **다리:** HTTP 스레드의 `ClaudeAgentLLM.complete()`(`:410`).
   - `asyncio.run_coroutine_threadsafe(s.submit(turn), s.loop)`로 루프에 일을 맡긴다(`:191`–`197`, `:420`). `submit`이 future를 만들어 큐에 넣는다(`:323`–`328`).
   - 그 결과를 `concurrent.futures.Future.result(timeout=8.0)`으로 기다린다(`:424`).
4. **직렬화는 세 겹이다.**
   - 서버 쪽: `TurnOrchestrator`의 `threading.Lock`이 턴, 세션 시작, 세션 끝을 하나씩만 들인다(`server/turn.py:198`, `:260`).
   - 액터 쪽: 큐가 들어온 순서대로 하나씩 보낸다.
   - 화면 쪽: 리듀서가 생각·말하는 중의 탭을 무시한다(`web/js/ui_state.js:48`). 그사이 들어온 최종 인식 결과도 새 턴을 열지 않는다(`:58`).
5. **시간 초과:** `.result(timeout)`이 시간 초과를 내면 다음 순서로 처리한다(`:425`–`428`, `:330`–`333`).
   - 기다림을 취소하고, 루프에 `abandon(turn)`을 맡긴다.
   - 그 턴이 줄 서 있었다면 보내지 않고 버린다(`:255`–`256`).
   - 이미 보내는 중이었다면 `client.interrupt()`를 보낸다.
   - 액터는 그 턴의 응답을 `ResultMessage`까지 끝까지 읽어 버린다(`break` 금지, `:295`). 남은 줄이 다음 턴의 답으로 섞이지 않게 하려는 것이다.

## Options Considered

### 방식 A: `ThreadingHTTPServer` + 세션 전용 asyncio 액터 스레드 (채택)

| 기준 | 평가 |
|---|---|
| 복잡도 | 중간. 스레드 경계 하나, 큐 하나, future 두 종류(asyncio, concurrent) |
| 비용 | 의존성 0개 추가 |
| 아이 경험(지연·안전) | ack가 LLM보다 먼저 나간다(11–16 ms). 시간 초과는 8초에 정확히 끊긴다 |
| 이해하기 | 중간. "동기 세계와 비동기 세계 사이의 우체통"으로 설명된다 |

**장점:**
- HTTP 처리, 정적 파일, 경로 탈출 검사는 모두 익숙한 동기 코드 그대로다.
- SDK는 자기 루프 안에서만 산다. 한 async 컨텍스트라는 SDK 제약을 그대로 지킨다.
- 시간 초과가 `.result(timeout)` 하나로 정확하다.
- 세션 하나 = 스레드 하나 = 루프 하나 = 클라이언트 하나라서, 세션을 바꾸면 통째로 갈아 끼우면 된다.

**단점:**
- future가 두 종류다: 루프 안의 `asyncio.Future`와 HTTP 스레드가 받는 `concurrent.futures.Future`.
- 루프가 닫힌 뒤 코루틴을 맡기면 `RuntimeError`가 난다. 그래서 `_schedule`이 이를 잡아 코루틴을 닫는다(`:191`–`197`).
- 액터가 아주 짧은 순간에 죽으면 `LLMError` 대신 시간 초과로 끝나는 경합이 하나 남아 있다. `complete()`가 "액터가 죽었나"를 확인한 바로 뒤, 루프가 다 정리된 다음에 `submit`이 돌면 아무도 채우지 않는 future를 8초 동안 기다리기 때문이다. 어느 쪽이든 준비된 대사로 이어진다.

### 방식 B: 전부 asyncio로 (asyncio HTTP 서버)

| 기준 | 평가 |
|---|---|
| 복잡도 | 높음. 표준 라이브러리에는 asyncio HTTP 서버가 없다. `asyncio.start_server`로 HTTP 파싱, 정적 파일, 오류 응답을 손으로 짜야 한다 |
| 비용 | aiohttp 같은 프레임워크를 쓰면 의존성 규칙 위반 |
| 아이 경험 | A와 같다 |
| 이해하기 | 서버 전체가 async라서 부모가 따라갈 개념이 늘어난다 |

**장점:** 스레드 경계가 없다. SDK와 같은 루프에서 돈다.
**단점:** 바퀴를 다시 만들거나 의존성 규칙을 어긴다. 아이 한 명짜리 서버에 비해 너무 크다.

### 방식 C: 핸들러 안에서 턴마다 `asyncio.run()`

| 기준 | 평가 |
|---|---|
| 복잡도 | 낮음 |
| 비용 | 0 |
| 아이 경험 | 느림. 턴마다 루프가 새로 생기고 끝나므로 클라이언트를 세션 동안 쥘 수 없다. 결국 턴마다 연결하는 ADR-0001의 방식 B가 된다(연결 0.7~2.0초가 매 턴 더해짐) |
| 이해하기 | 쉬움 |

**장점:** 코드가 가장 짧다.
**단점:** SDK 제약 때문에 상주 클라이언트와 같이 쓸 수 없다. ADR-0001의 결정과 충돌한다.

### 방식 D: 서버 전체에 루프 스레드 하나만 두고 세션을 계속 바꿔 끼우기

- spec 5.3의 문장("LLM 전용 스레드 하나")에 가장 가깝다.
- 구현(Task 4)에서는 **세션마다** 스레드와 루프를 새로 만들었다.
  - 세션을 끝내면 그 스레드가 루프를 정리하고 끝난다(`:233`–`242`). 클라이언트, 큐, future가 섞일 걱정이 없다.
  - `start_session`은 먼저 앞 세션을 끝낸다(`:391`). 그래서 동시에 살아 있는 액터는 언제나 많아야 하나이고, spec이 뜻한 "LLM 전용 스레드 하나"와 같다.

## Trade-off Analysis

- **A의 핵심 거래:** 스레드 경계 하나를 받아들인다. 그 대신 서버의 나머지(정적 파일, 경로 탈출 차단, 오류 응답, 테스트용 `make_server`)를 표준 라이브러리 동기 코드로 둔다.
- **스레드 경계는 한 곳뿐이다.** `complete()` 한 함수에만 있고, 바깥(`turn.py`)은 `LLM` 프로토콜의 보통 함수만 본다. `FakeLLM`도 같은 모양이라서, 턴 처리 테스트는 asyncio를 전혀 모른 채 돈다(`server/llm/__init__.py:42`).
- **B는** 이 앱의 규모(아이 한 명, 127.0.0.1)에 비해 비싸다. **C는** ADR-0001과 함께 쓸 수 없다.

## Consequences

- **쉬워지는 것:**
  - 턴 처리 코드(`server/turn.py`)가 동기 함수와 제너레이터만으로 읽힌다.
  - ack 줄을 LLM보다 먼저 보내는 것이 `yield` 한 줄이다(`server/turn.py:264`).
  - 테스트: 가짜 `ClaudeSDKClient`를 넣어 액터의 시간 초과, interrupt, 줄 선 턴 버리기, 충돌을 쿼터 없이 시험한다(`tests/py/test_claude_agent_options.py:236`, `:248`, `:301`).
- **어려워지는 것:**
  - 서버의 잠금이 ack 줄의 `yield`를 지나서까지 잡혀 있다. 그래서 동시에 들어온 두 번째 턴은 **ack까지** 첫 턴이 끝날 때까지(최대 약 8초) 기다린다. 잠금을 첫 줄 앞에서 놓지 않은 것은 일부러다. M2의 첫 줄(fast)이 세션 상태(부화)를 바꾸므로 그 계산까지 잠금 안에 있어야 한다. 화면 리듀서가 연달아 누르기를 막으니 실제로는 생기지 않는다.
  - 잠금을 쥔 채 ack를 `yield`하므로, 핸들러는 `finally`에서 반드시 `lines.close()`를 불러 제너레이터를 닫는다(`server/app.py:182`–`183`). 아이 화면이 언제 떠났는지에 따라 결과가 다르다.
    - **ack 줄을 쓰다가 실패하면**(`server/app.py:177`에서 쓰기 오류): `lines.close()`가 `yield` 자리에서 제너레이터를 끝내며 잠금을 푼다. LLM은 불리지 않고 로그도 쓰지 않는다.
    - **ack 쓰기가 성공한 뒤에 아이 화면이 떠나면:** 서버는 아직 그 사실을 모르고 `server/app.py:178`에서 제너레이터를 다시 돌린다. 턴은 끝까지 간다. LLM을 부르고(`server/turn.py:270` → `:311`, 쿼터를 쓴다), 세션 로그에 턴 한 줄을 쓴다(`server/turn.py:273`). 버려지는 것은 reply 줄 쓰기뿐이다. 그 쓰기 오류(BrokenPipe 등)는 `server/app.py:180`–`181`이 조용히 삼킨다.
    - 이 경우를 지키는 회귀 테스트(최종 리뷰에서 더함): 1줄만 읽고 RST로 끊는 클라이언트 뒤에 잠금이 풀려 있고, 다음 턴이 정확히 두 줄로 나간다(`tests/py/test_api_turn.py:110`). 잠금을 reply `yield` 너머까지 쥐고 `finally` 없이 풀도록 바꿔 보면 이 테스트가 실패한다(2026-10-25에 해 봄).
  - `end_session`은 액터 스레드를 최대 5초 기다린다(`:437`–`444`). bench에서는 474 ms였다.
- **나중에 다시 볼 것:**
  - M2의 fastpath 줄(`fast`)은 이 잠금 안에서 첫 줄을 계산한다. 그래서 부화 상태 같은 세션 상태를 안전하게 바꿀 수 있다(Task 13에서 그렇게 했다, `server/turn.py:260`–`269`).
  - 여러 탭이나 여러 아이를 지원하게 되면(지금은 비목표) 세션별 잠금과 세션별 액터를 다시 설계한다.

## Action Items

1. [x] `_Session` 액터, `complete`의 다리, 시간 초과 → interrupt → result까지 읽기 (Task 4)
2. [x] `TurnOrchestrator` 잠금과 직렬화 테스트 (`tests/py/test_turn.py:113`, Task 6)
3. [x] ack가 LLM 전에 flush되는지 보는 시간 테스트 (`tests/py/test_api_turn.py:88`)
4. [x] M2 Task 13: fastpath 첫 줄을 같은 잠금 안에서 계산 (`server/turn.py:260`–`264`)
