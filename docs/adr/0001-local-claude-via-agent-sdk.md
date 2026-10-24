# ADR-0001: 로컬 Claude Code를 Agent SDK로 상주시켜 부른다

**Status:** Accepted
**Date:** 2026-10-25 (방향은 2026-10-22 spec 5.2에서 정했고, 숫자는 2026-10-24 Task 4 bench에서 쟀다)
**Deciders:** 부모(개발자). 설계 spec 5.2, 구현과 측정 Task 4, 요금 지킴이 Task 6

줄 번호는 M2 끝(커밋 `37952d8`)의 실제 줄로 다시 맞췄다(Task 16). `server/llm/claude_agent.py`와 `server/config.py`는 M1 뒤로 바뀌지 않았다.

> 한 줄로: `claude.exe` 하나를 세션 동안 띄워 두고, 턴마다 stream-json 한 줄을 보낸다. 그 `claude.exe`는 이 PC의 개발용 설정을 하나도 보지 못하게 막고, 정말 막혔는지 세션 시작 정보(`init`)로 확인한다.

## Context

- **비용 0원(G3):** 이 PC의 Claude Code 구독(모델 haiku)만 쓴다. API 키로 과금되는 길은 어떤 경우에도 막는다(사용자 규칙).
- **지연(G1):** LLM 대사는 p50 2.5초, p95 4초 이하. 8초가 넘으면 준비된 대사로 넘어간다.
- **격리(반목표 A4):** 이 PC의 Claude Code에는 개발용 설정이 가득하다. Explanatory 출력 스타일, 플러그인(superpowers 등), 사용자 스킬, claude.ai 커넥터, `CLAUDE.md`, 자동 메모리. 이것이 아이 대화에 섞이면 안 된다([M0 워크스루 2절](../walkthrough/m0-scaffold.md#2-오늘의-개념-개발용-claude와-아이-대화용-claude는-다르다)).
- **서버가 켜지는 곳:** 부모는 서버를 Claude 데스크톱 앱의 미리보기(`.claude/launch.json`)나 터미널 패널에서 켠다. 그러면 서버 프로세스가 부모 Claude Code 세션의 환경변수를 물려받는다. Task 4에서 실제로 **30개**가 들어와 있었다.
- **환경:** Windows 11, 프로젝트 경로에 한글과 공백이 있다. 런타임 의존성은 `claude-agent-sdk==0.2.172` 하나다. 이 SDK의 Windows 휠에는 `claude.exe`(Claude Code CLI 2.1.308)가 들어 있어서 PATH를 신경 쓰지 않아도 된다.

## Decision

`ClaudeSDKClient` 하나를 **세션 동안 살려 두고**(`async with` 블록 하나가 세션 내내 이어진다) 턴마다 한 줄 메시지를 보낸다. 그 `claude.exe`는 아래 격리 옵션 전부, 환경변수 정리, `.env`의 구독 토큰, 요금 지킴이와 함께 돈다.

SDK가 만드는 실제 명령줄은 다음과 같다. 2026-10-25에 SDK의 `_build_command()`로 프로세스를 띄우지 않고 뽑았다(쿼터 0). 폴더 경로만 `%TEMP%`로 줄였다.

```text
claude.exe --output-format stream-json --verbose
  --system-prompt-file %TEMP%\kids_english_llm\system_prompt.md
  --tools "" --max-turns 1 --disallowedTools mcp__* --model haiku --permission-mode dontAsk
  --settings {"enabledPlugins": {"cc-plugin-agents-md@builtin": false, "cc-plugin-telemetry@builtin": false, "cc-plugin-plugin-authoring@builtin": false}}
  --strict-mcp-config --setting-sources= --no-session-persistence --disable-slash-commands
  --thinking disabled --input-format stream-json
```

- `--input-format stream-json`과 `--output-format stream-json`이 "상주"를 가능하게 한다. 표준 입력으로 JSON 한 줄을 보낼 때마다 한 턴이 돌고, 표준 출력으로 이벤트가 한 줄씩 나온다. 프로세스는 끝나지 않고 다음 줄을 기다린다([M1 워크스루 2절](../walkthrough/m1-walking-skeleton.md)).
- 코드: `server/llm/claude_agent.py:77`(`build_options`), `:208`(`_Session`), `:349`(`ClaudeAgentLLM`).

## Options Considered

### 방식 A: SDK `ClaudeSDKClient`를 세션 동안 상주 (채택)

| 기준 | 평가 |
|---|---|
| 지연 | 턴 p50 1890 ms, p95 2543 ms(bench 20턴). 프로세스를 띄우는 1969 ms는 세션을 열 때 한 번만, 아이가 처음 말하기 전에 낸다 |
| 비용 | 구독 쿼터만. API 키 경로는 요금 지킴이가 막는다 |
| 격리 | 옵션 전부 적용, `init`으로 확인해 모두 OK |
| 복잡도 | 중간. 동기 HTTP 스레드와 비동기 SDK 사이에 다리가 필요하다([ADR-0002](0002-stdlib-server-and-llm-actor.md)) |
| 이해하기 | 중간. "세션", "액터", "stream-json"이라는 새 개념 셋 |

**장점:**
- 턴마다 프로세스를 띄우지 않는다.
- 세션 안에서 대화 맥락이 이어진다. 멍멍이가 앞 턴에서 한 말을 기억한다.
- 시간이 넘으면 `interrupt()`로 그 턴만 멈출 수 있다. 프로세스는 그대로 산다.
- 의존성이 SDK 하나뿐이다. SDK 휠에 `claude.exe`가 들어 있다.

**단점:**
- 세션의 시작과 끝을 관리해야 한다(`start_session`, `end_session`, implicit start).
- `claude.exe`가 죽으면 다음 세션을 열 때까지 모든 턴이 준비된 대사로 답한다(자동 재시작 없음).
- 세션의 첫 턴은 느리다(bench 3843 ms, 브라우저 3756 ms).

### 방식 B: 턴마다 `claude -p` 실행

| 기준 | 평가 |
|---|---|
| 지연 | 턴마다 프로세스 시작과 초기화를 다시 낸다. 우리가 잰 연결 시간 0.7~2.0초(735, 852, 1969 ms)를 턴 p50 1.9초에 더하면 **약 2.6~3.9초**로, p50 목표 2.5초를 넘는다(계산값). spec 5.2는 설계 때 3~7초로 보았다 |
| 비용 | 구독 쿼터만 |
| 격리 | A와 같은 인자를 줄 수 있다 |
| 복잡도 | 낮음. 턴마다 하위 프로세스 하나 |
| 이해하기 | 쉬움 |

**장점:** 구조가 가장 단순하다. 스레드나 asyncio가 필요 없다.
**단점:**
- 느리다.
- 턴 사이에 대화가 이어지지 않는다. 이으려면 `--resume`처럼 디스크의 대화 기록을 다시 읽어야 한다. 이것은 아이 대화를 디스크에 남기지 않는다는 원칙(`--no-session-persistence`, spec 7절)과 맞지 않는다.
- 턴마다 새 프로세스라서 시간 초과 때 할 수 있는 일이 프로세스 죽이기뿐이다.

### 방식 C: `--bare` 모드

| 기준 | 평가 |
|---|---|
| 지연 | A나 B와 비슷 |
| 비용 | **쓸 수 없다.** bare 모드는 구독 OAuth를 읽지 않는다. 그러면 API 키가 필요하고, 그것은 G3 위반이다 |
| 격리 | 설정을 덜 읽는 모드라 격리에는 유리하다 |
| 복잡도 | 낮음 |
| 이해하기 | 쉬움 |

**장점:** 플래그 하나로 많은 것을 끈다.
**단점:**
- 구독 인증을 쓰지 못한다(spec 5.2).
- Task 4에서 같은 효과가 **실수로** 생길 수 있다는 것도 확인했다. 부모 Claude Code가 물려주는 `CLAUDE_CODE_SIMPLE=1`이 bare 모드다. 그래서 아래 "환경변수 정리"가 이 변수를 지운다.

### (참고) 방식 D: Anthropic API 직접 호출

- 유료라서 G3에 맞지 않는다.
- spec 5.4의 `AnthropicApiLLM` 자리만 남기고 만들지 않는다.
- 어댑터 인터페이스(`server/llm/__init__.py:42`)가 같아서, 나중에 바꿔도 턴 처리 코드는 그대로다.

## 격리 옵션 전체

`build_options`(`server/llm/claude_agent.py:77`–`103`)가 만드는 옵션이다. 마지막 칸은 bench가 `init`에서 실제로 본 값이다.

| 무엇을 막나 | SDK 옵션 | CLI 인자 | 확인한 것 |
|---|---|---|---|
| Claude Code 기본 프롬프트(코딩 도우미 역할) | `system_prompt={"type":"file", ...}`: 서버가 채운 `prompts/chomp_system.md`를 작업 폴더에 쓰고 그 파일을 준다 | `--system-prompt-file` | 답 20/20이 JSON 객체 하나 |
| 내장 도구(파일, 셸 등) | `tools=[]`, `allowed_tools=[]` | `--tools ""` | `tools: []` |
| MCP 도구, claude.ai 커넥터 | `disallowed_tools=["mcp__*"]`, `mcp_servers={}`, `strict_mcp_config=True`, 환경변수 `ENABLE_CLAUDEAI_MCP_SERVERS=false` | `--disallowedTools mcp__*`, `--strict-mcp-config` | `mcp_servers: []` |
| 사용자·프로젝트·로컬 설정 파일(출력 스타일, `CLAUDE.md`, 훅, 스킬, 사용자 플러그인) | `setting_sources=[]` | `--setting-sources=` | `output_style: default` |
| CLI에 들어 있는 기본 플러그인 3개 | `settings=json.dumps({"enabledPlugins": {"<이름>@builtin": false, ...}})` | `--settings {...}` | `plugins: []` (넣기 전에는 3개, 아래) |
| 스킬과 슬래시 명령 | `extra_args={"disable-slash-commands": None}` | `--disable-slash-commands` | `skills: 0`, `slash_commands: 0` |
| 대화 기록 파일 | `extra_args={"no-session-persistence": None}` | `--no-session-persistence` | 세션 뒤 `~/.claude/projects/`에 이 앱 폴더가 생기지 않음 |
| 혼자 여러 단계 이어 가기 | `max_turns=1` | `--max-turns 1` | |
| 허락 묻기 | `permission_mode="dontAsk"` | `--permission-mode dontAsk` | `permissionMode: dontAsk` |
| 사고 과정(지연) | `thinking={"type":"disabled"}` | `--thinking disabled` | API가 받아들임(아래) |
| 모델 | `model="haiku"` | `--model haiku` | `model: claude-haiku-5-5` |
| 프로젝트 폴더의 파일 | `cwd`=빈 ASCII 임시 폴더 `%TEMP%\kids_english_llm`. 임시 폴더 경로가 ASCII가 아니면 `C:\kids_english_llm`. bench는 `kids_english_llm_bench`를 따로 쓴다 | 프로세스 작업 폴더 | `cwd`가 그 폴더 |
| 자동 메모리, `CLAUDE.md` 자동 로딩, 첨부, git 안내 | 환경변수 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_ATTACHMENTS=1`, `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` | 자식 프로세스 환경 | |
| (효과 없음, 그대로 둠) | 환경변수 `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1` | | SDK는 이 값을 자식이 아니라 **서버 자신의** 환경에서 읽는다. 건너뛰려던 버전 확인은 세션마다 약 0.1초라 그냥 둔다(`server/llm/claude_agent.py:47`–`48`) |

- `claude.exe`의 stderr는 서버 콘솔로 보내지 않고 최근 40줄만 메모리에 둔다(`server/llm/claude_agent.py:214`–`215`). 진단은 bench가 보여 준다.
- 계약 테스트 `tests/py/test_claude_agent_options.py:33`(`test_build_options_isolates_everything`)가 위 옵션 값을 쿼터 없이 고정한다.

### 기본 플러그인 3개를 `--settings`로 끈 이야기

- **무엇이 보였나:** Task 4의 첫 격리 점검에서 `plugins`만 FAIL이었다. `cc-plugin-agents-md`, `cc-plugin-telemetry`, `cc-plugin-plugin-authoring` 세 개였다.
- **사용자 플러그인이 아니다.** `claude.exe`에 컴파일돼 들어 있는 기본(builtin) 플러그인이다. superpowers나 `~/.claude/plugins`의 것은 하나도 없었다. 그래서 `setting_sources=[]`로는 꺼지지 않는다.
- **각각 하는 일:**
  - `agents-md`: `AGENTS.md` 파일을 읽어 들인다. `CLAUDE.md`와 같은 길이라 격리에서 **반드시** 꺼야 한다.
  - `telemetry`: 사용 통계.
  - `plugin-authoring`: 모드 만들기 스킬. 도구·스킬·슬래시 명령이 모두 0이라 어차피 닿지 않는다.
- **어떻게 껐나:** CLI는 병합된 설정의 `enabledPlugins["<이름>@builtin"]`으로 기본 플러그인을 켜고 끈다(CLI 2.1.308 코드에서 확인). 그래서 사용자 설정 파일 대신 `--settings`로 JSON 하나만 준다. 이름은 `init.plugins`에 보인 그대로 썼다(`server/llm/claude_agent.py:61`–`64`).
- **결과:** `plugins: []`. 격리 판정(`check_isolation`)은 허용 목록 없이 엄격한 그대로 두었다.
- **주의:** 정책으로만 켜고 끄는 기본 플러그인(`cc-plugin-sec-default` 등)은 이 스위치를 무시한다. 지금 `init`에는 없다. CLI 버전이 바뀌면 bench로 다시 확인한다.

## 환경변수 정리 (`scrub_llm_env`)

- **무엇을:** 서버(`run.py:64`)와 bench가 시작할 때 이름이 `ANTHROPIC_` 또는 `CLAUDE`로 시작하는 변수를 모두 지운다. 지운 **이름만** 출력하고 값은 어디에도 쓰지 않는다(`server/config.py:57`–`70`).
- **남기는 셋**(사용자가 일부러 정하는 값):
  - `CLAUDE_CODE_OAUTH_TOKEN`: 직접 받은 구독 토큰
  - `CLAUDE_CODE_GIT_BASH_PATH`: Windows의 Git Bash 위치
  - `CLAUDE_CONFIG_DIR`: 설정 폴더 위치
- **왜 필요한가:**
  - SDK의 `options.env`는 자식 환경에 변수를 **더하거나 덮어쓸** 수만 있다. 지울 수는 없다. 그래서 자식이 물려받기 전에 서버 자신의 환경에서 지운다.
  - Claude Code 안에서 켠 서버는 부모 세션의 변수 약 30개를 물려받는다(Task 4 bench에서 30개를 지웠다). 그중 문제가 되는 것:
    - `CLAUDE_CODE_SIMPLE=1`: bare 모드. 구독 OAuth를 읽지 않아서 인증이 깨진다(방식 C).
    - `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_CODE_MESSAGING_TOKEN`: 아이용 `claude.exe`를 부모 세션에 잇는다.
    - `CLAUDE_CODE_TERMINAL_MCP_TOOLS`: MCP 도구.
    - `CLAUDE_EFFORT`: 노력 수준을 바꾼다.
  - `ANTHROPIC_` 변수도 지운다:
    - `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`: 구독 OAuth보다 먼저 쓰여 요금이 나간다.
    - `ANTHROPIC_BASE_URL`: 요청을 다른 곳으로 보낸다.
    - `ANTHROPIC_DEFAULT_HAIKU_MODEL` 같은 값: "haiku"가 가리키는 모델을 바꾼다.
- 테스트: `tests/py/test_config.py:43`(`test_scrub_llm_env_removes_auth_and_parent_claude_session_vars`).

## 인증

- **spec의 기본값은** "이미 로그인된 구독 자격증명"이었다.
- **실제로는** Task 4에서 CLI에 저장된 로그인(`~/.claude/.credentials.json`)이 만료돼 있었다. 메시지는 "OAuth session expired and could not be refreshed"였다.
- **그래서 지금 쓰는 길:** 사용자가 직접 `claude setup-token`으로 받은 토큰을 프로젝트의 `.env`에 `CLAUDE_CODE_OAUTH_TOKEN=`으로 넣었다.
  - `load_config`가 읽는다. 우선순위는 환경변수 > `.env`다.
  - `build_options`가 값이 있을 때만 **자식 환경에만** 넣는다(`server/llm/claude_agent.py:81`–`83`).
  - 토큰 값은 `Config`의 repr(`server/config.py:31`), `ClaudeAgentLLM`의 repr, 로그, 출력 어디에도 나오지 않는다. bench는 "given via .env/environment"라고 있다는 사실만 출력한다.
- 이 토큰에는 claude.ai 커넥터가 따라오지 않아서 한 겹 더 안전하다.
- `.env`가 비어 있으면 CLI에 저장된 로그인으로 돌아간다.
- `.env`는 git에 올리지 않는다. 토큰을 받고 넣는 일은 언제나 사용자가 직접 한다.

## 요금 지킴이 (API 키 과금 거부, 끝까지 유지)

줄 번호만 적은 것은 `server/llm/claude_agent.py`의 줄이다.

- **판정:** 세션 시작 정보 `init`의 `apiKeySource`가 정확히 `"none"`일 때만 통과한다. `"none"`은 API 키 없음, 곧 구독 OAuth라는 뜻이다. 값이 없거나 `"ANTHROPIC_API_KEY"`, `"apiKeyHelper"`, `"/login managed key"` 같은 다른 값이면 거부한다. 모르면 막는다(`server/llm/claude_agent.py:152`).
- **언제:** 턴마다 오는 `init`을 모두 본다(`:297`–`303`). 거부하면 바로 `interrupt()`로 답 생성을 멈추고, 그 턴의 result 줄까지는 읽는다. 그다음 액터가 `async with`를 빠져나가 `claude.exe`를 닫는다(`:260`–`266`).
- **끝까지 유지된다(sticky):**
  - 거부는 그 `ClaudeAgentLLM` 객체가 사는 동안 그대로다.
  - 그 뒤의 `start_session`은 `claude.exe`를 다시 띄우지 않는다(`:392`–`393`).
  - `complete`는 질의를 보내지 않고 바로 `LLMBillingRefused`를 낸다(`:411`–`412`).
  - 아이에게는 준비된 대사가 나간다.
  - 서버 콘솔에는 정해진 한 줄 "LLM refused: API-key billing (subscription only; fix auth, restart)"이 찍힌다. 세션 로그에는 클래스 이름 `LLMBillingRefused`만 남는다(`server/turn.py:306`–`307`).
  - 다시 쓰려면 인증을 고치고 서버를 다시 켠다. 새 객체가 `init`을 새로 판정한다.
  - Task 6 첫 구현에서는 거부가 새 세션마다 풀렸다. 그래서 페이지를 다시 열 때마다 유료 질의가 하나씩 나갈 수 있었고, 리뷰에서 고쳤다(`f1a69db`).
- **한계:**
  - `init`은 턴의 응답 맨 앞에 온다. 그래서 첫 질의는 출처를 알기 전에 이미 나간다. 유료 질의는 **서버를 켤 때마다 많아야 한 번**이다.
  - 실제로는 환경변수 정리가 `ANTHROPIC_API_KEY`를 먼저 지우고 `.env`가 구독 토큰을 주므로, 지킴이는 두 번째 방어선이다.
- 테스트: `tests/py/test_claude_agent_options.py:341`, `:363`, `tests/py/test_turn.py:279`.

## bench 결과 (Task 4, 2026-10-24)

명령: `.venv/Scripts/python scripts/bench_llm.py --turns 20` (종료 코드 0). 쓴 쿼터는 haiku 약 23턴이다(bench 20 + live 테스트 약 3).

**연결과 종료**
- `start_session` 반환 5 ms. 연결을 기다리지 않는다.
- 연결 1969 ms(`claude.exe` 시작 + initialize).
- `end_session` 474 ms.

**격리 점검표**

| key | 결과 | 값 |
|---|---|---|
| tools | OK | `[]` |
| mcp_servers | OK | `[]` |
| plugins | OK | `[]` (`--settings` 전에는 FAIL: 3개) |
| model | OK | `claude-haiku-5-5` |
| output_style | OK | `default` |

판정하지 않고 참고로 본 값:
- `apiKeySource: none`, `permissionMode: dontAsk`, `cwd: %TEMP%\kids_english_llm`, `claude_code_version: 2.1.308`
- `skills: 0`, `slash_commands: 0`
- `agents: 5`: CLI 내장 하위 에이전트 종류다. `tools=[]`라서 부를 길이 없다.

**턴별 지연(ms)**

| # | 입력 | ms | | # | 입력 | ms |
|---|---|---|---|---|---|---|
| 1 | hello chomp | 3843 | | 11 | i like dinosaurs | 1870 |
| 2 | jump | 1937 | | 12 | 공 차 | 2375 |
| 3 | kick the ball | 1799 | | 13 | two blue balls | 1548 |
| 4 | 빨간 알 | 1685 | | 14 | stretch your arms | 1936 |
| 5 | what is your name | 2475 | | 15 | where is the egg | 1946 |
| 6 | turn around | 1735 | | 16 | spin | 1962 |
| 7 | big red egg | 1620 | | 17 | yawn | 1973 |
| 8 | kick ball | 2015 | | 18 | let's dance | 1826 |
| 9 | bark | 1909 | | 19 | give me five | 1800 |
| 10 | thumbs up | 1627 | | 20 | good night | 1838 |

- **p50 1890 ms, p95 2543 ms.** 최소 1548, 최대 3843, 평균 1986.
- **목표를 둘 다 맞췄다**(G1: p50 ≤ 2500, p95 ≤ 4000). 그래서 G1의 대응(fastpath 넓히기, 프롬프트 줄이기, `say` 먼저 읽기)은 지금 적용하지 않는다.
- 1번 턴(3843 ms)은 세션의 첫 API 호출이라 느리다(콜드).
- 형식: 20턴 모두 JSON 객체 하나로 답했다.
- 지연(`latency_ms`)은 `complete()`를 부른 때부터 결과까지다. 큐 대기가 들어 있다.

**같은 결과를 다른 곳에서도 봤다**
- `pytest -m live` 2개 통과(12.18초):
  - 한 턴이 답하고 격리가 OK다.
  - 0.05초 시간 초과는 `LLMTimeout` → interrupt → 같은 `claude.exe`에서 다음 턴이 정상으로 답한다. stream-json이 어긋나지 않는다는 뜻이다.
- 화면(Task 7, 미리보기 `?debug=1`, 실제 Claude 3턴): reply 3756 ms(세션 첫 턴), 2036 ms, 1533 ms. ack는 11–16 ms.
- M1 마일스톤 재확인(Task 8, 2026-10-25, 실제 Claude 1턴): "hello chomp" → ack 12 ms, reply 2419 ms, `source=llm`, 영어 답.

## thinking 옵션 처리 결과

- `thinking={"type":"disabled"}`는 숨은 CLI 플래그 `--thinking disabled`가 된다. `--help`에는 나오지 않는다.
- CLI가 받아들였고, haiku의 API도 받아들였다. bench 20턴이 모두 정상 결과를 냈다.
- 그래서 미리 정해 둔 대안(`effort="low"`)은 쓰지 않았다. `effort`는 None 그대로다.

## Trade-off Analysis

- **A를 고른 까닭:** 프로세스 시작 비용을 턴에서 세션으로 옮기는 것이 가장 크다. 아이가 말할 때마다 내는 대신, 페이지를 열 때 한 번 낸다.
- **A의 대가:**
  - 복잡도: 스레드 하나와 이벤트 루프 하나, 세션 수명 관리([ADR-0002](0002-stdlib-server-and-llm-actor.md)).
  - CLI 버전 의존: 숨은 플래그와 기본 플러그인 이름은 CLI 2.1.308에서 확인한 것이다.
- **B는** 가장 이해하기 쉽지만 p50 목표를 넘는다. 대화가 이어지지 않는 것도 아이와의 놀이에는 큰 손해다.
- **C는** 격리에는 좋지만 구독 인증을 쓸 수 없다. 비용 원칙 앞에서 탈락이다.

## Consequences

- **쉬워지는 것:**
  - 턴 지연 p50 1.9초.
  - 세션 안에서 대화 맥락이 이어진다.
  - 시간 초과된 턴만 interrupt로 끊는다.
  - 격리를 `init`으로 눈으로 확인할 수 있다(`scripts/bench_llm.py`).
- **어려워지는 것:**
  - HTTP 스레드(동기)와 SDK(비동기) 사이의 다리가 필요하다(ADR-0002).
  - 세션이 열린 동안 `claude.exe` 하나가 늘 떠 있다.
  - `claude.exe`가 죽으면 자동으로 다시 띄우지 않는다. 다음 세션을 열 때까지(새로고침이나 새 session_id) 모든 턴이 준비된 대사로 답한다.
- **나중에 다시 볼 것:**
  - SDK나 번들 CLI 버전을 올릴 때: bench를 다시 돌린다(사용자 승인 후, haiku 약 20턴). 격리 점검표가 모두 OK이고 종료 코드가 0인지 확인한다. 숨은 플래그(`--thinking`)와 `@builtin` 이름이 바뀔 수 있다.
  - M4의 15분 세션: 대화가 길어질수록 턴마다 실리는 맥락도 늘어난다. bench 20턴 안에서는 지연이 늘지 않았다(16–20번 1800–1973 ms). 15분 세션에서 다시 잰다.
  - p50이 2.5초를 넘기 시작하면 G1의 대응(fastpath 넓히기, 프롬프트 줄이기, `say` 먼저 읽기)을 고른다.
    - **M2에서 넘었다**(2026-10-25 미리보기, LLM 턴 5개 p50 2.7초, 최대 3.4초). 시스템 프롬프트가 3,439자에서 9,437자로 늘었다. 고른 대응과 숫자는 [ADR-0004](0004-fastpath.md)의 "LLM 지연"에 있다.

## Action Items

1. [x] `build_options`와 격리 계약 테스트 (`tests/py/test_claude_agent_options.py:33`)
2. [x] `scripts/bench_llm.py`: 격리 점검표, 20턴 p50/p95, 격리 실패 시 종료 코드 2로 바로 멈춤
3. [x] 기본 플러그인 3개 끄기(`--settings`), 환경변수 정리, `.env` 토큰
4. [x] 요금 지킴이와 거부 유지(`f1a69db`)
5. [ ] SDK·CLI를 올릴 때마다 bench 다시 돌리기(사용자 승인 후)
6. [ ] M4: 15분 세션 동안 지연이 어떻게 변하는지 재기
7. [ ] (필요해지면) `claude.exe`가 죽었을 때 다음 턴에서 세션을 다시 여는 자동 재시작
