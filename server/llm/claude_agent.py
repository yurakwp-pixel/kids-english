"""진짜 Claude(이 PC 의 Claude Code 구독, 모델 haiku)에 붙는 LLM 어댑터.

구조 (spec 5.2–5.4, ADR-0001/0002):
- `ClaudeSDKClient` 는 연결부터 종료까지 같은 async 컨텍스트 안에서 써야 한다. 그래서 세션마다 전용 데몬 스레드
  하나가 자기 asyncio 이벤트 루프를 돌리고, 그 안의 액터 코루틴 하나가 `async with ClaudeSDKClient(options)` 를
  세션 내내 쥔다. claude.exe 프로세스 하나가 세션 동안 살아 있으니 턴마다 프로세스를 띄우는 비용이 없다.
- HTTP 스레드는 `complete()` 를 부른다. 이 함수는 `run_coroutine_threadsafe(...).result(timeout_s)` 로 액터의
  받은편지함(asyncio.Queue)에 턴을 넣고 답을 기다린다. 턴은 들어온 순서대로 하나씩 처리된다.
- 한 턴: `query()` 다음 `receive_response()` 를 ResultMessage 까지 끝까지 읽는다. 중간에 `break` 하지 않는다.
  빠져나오면 남은 메시지가 다음 턴의 답으로 섞인다. 시간이 넘으면 `interrupt()` 로 그 턴을 빨리 끝내게 하고,
  액터는 그 턴의 끝(ResultMessage)까지 읽어 버린 뒤 다음 턴으로 간다.

격리 (반목표 A4): `build_options` 가 기본 프롬프트, 도구, MCP, 사용자 설정(출력 스타일·CLAUDE.md·훅·스킬·플러그인),
대화 기록 저장을 모두 끈다. 실제로 꺼졌는지는 세션 시작 정보(`init_info`)를 `check_isolation` 으로 확인한다.
서버 프로세스 환경에서 물려받으면 안 되는 변수는 `server.config.scrub_llm_env` 가 시작할 때 지운다.

요금 (사용자 규칙 "API 요금 0원"): init 의 `apiKeySource` 가 "none" 이 아니면 `billing_ok` 가 거부하고, 액터가 그 턴을
interrupt 한 뒤 세션을 끝낸다. 거부는 그 `ClaudeAgentLLM` 객체가 사는 동안 유지된다: 뒤의 `start_session` 은 claude.exe 를
다시 띄우지 않고, `complete()` 는 질의 없이 바로 `LLMBillingRefused` 를 낸다(놀이는 준비된 대사로 이어진다).
그래서 유료 질의는 서버를 켤 때마다 많아야 한 번이다. 다시 쓰려면 인증을 고친 뒤 서버를 다시 켠다.
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import json
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from server.llm import LLM, LLMBillingRefused, LLMError, LLMResult, LLMTimeout

# 아이용 claude.exe 에만 주는 환경변수. 자동 메모리, CLAUDE.md, 첨부, claude.ai 커넥터, git 안내를 끈다.
# CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK 는 SDK 가 부모(이 서버) 환경에서 읽는 값이라 여기서는 효과가 없다.
# 버전 확인(claude.exe -v)은 세션 시작 때 한 번, 약 0.1초라 그대로 둔다.
ISOLATION_ENV: dict[str, str] = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
    "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
}

# CLI(2.1.308)에 들어 있는 기본 플러그인. setting_sources=[] 로도 꺼지지 않아 init.plugins 에 나타났다.
# CLI 는 settings 의 enabledPlugins["<이름>@builtin"] 으로 켜고 끈다. agents-md 는 AGENTS.md 를 읽어 들이므로
# 반드시 끈다(CLAUDE.md 와 같은 길). 이름은 init.plugins 에 보인 그대로다.
BUILTIN_PLUGINS_OFF = ("cc-plugin-agents-md", "cc-plugin-telemetry", "cc-plugin-plugin-authoring")
ISOLATION_SETTINGS: dict[str, Any] = {
    "enabledPlugins": {f"{name}@builtin": False for name in BUILTIN_PLUGINS_OFF},
}

# 작업 폴더는 ASCII 여야 한다(프로젝트 경로의 한글·공백이 CLI 인자와 프로젝트 기록 폴더 이름에 섞이지 않게).
_FALLBACK_WORKDIR = Path("C:\\kids_english_llm")
SYSTEM_PROMPT_FILE = "system_prompt.md"


def default_workdir() -> Path:
    """아이용 Claude 가 도는 빈 작업 폴더: `%TEMP%\\kids_english_llm`, 임시 폴더 경로가 ASCII 가 아니면 `C:\\kids_english_llm`."""
    path = Path(tempfile.gettempdir()) / "kids_english_llm"
    return path if str(path).isascii() else _FALLBACK_WORKDIR


def build_options(
    model: str, system_prompt_path: Path, workdir: Path, oauth_token: str | None
) -> ClaudeAgentOptions:
    """아이 대화용 옵션. 사용자 설정과 도구를 하나도 보지 못하게 한다. 토큰은 주어질 때만 env 에 넣는다."""
    env = dict(ISOLATION_ENV)
    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    return ClaudeAgentOptions(
        model=model,
        # 기본 Claude Code 프롬프트를 통째로 바꾼다(--system-prompt-file).
        system_prompt={"type": "file", "path": str(system_prompt_path)},
        tools=[],                      # 내장 도구 없음(--tools "")
        allowed_tools=[],
        disallowed_tools=["mcp__*"],   # 혹시 붙은 MCP 도구도 거부
        mcp_servers={},
        strict_mcp_config=True,        # --mcp-config 로 준 것(없음)만 쓴다
        setting_sources=[],            # user/project/local 설정 파일을 읽지 않는다
        permission_mode="dontAsk",     # 미리 허락되지 않은 것은 묻지 않고 거부
        max_turns=1,
        thinking={"type": "disabled"},
        # 번들 CLI 2.1.308 의 --help 에 둘 다 있다: 대화 기록을 디스크에 남기지 않고, 스킬과 슬래시 명령을 끈다.
        extra_args={"no-session-persistence": None, "disable-slash-commands": None},
        # 사용자 설정 파일 대신 이 JSON 하나만 준다(--settings): 기본 플러그인을 끈다.
        settings=json.dumps(ISOLATION_SETTINGS),
        cwd=workdir,
        env=env,
    )


# --- 격리 확인 --------------------------------------------------------------------------------

@dataclass(frozen=True)
class IsolationCheck:
    key: str
    shown: str  # 표에 보일 요약. 이름과 개수뿐, 비밀 값은 없다.
    ok: bool


def _summary(value: Any) -> str:
    if value is None:
        return "(missing)"
    if isinstance(value, list):
        if not value:
            return "[] (empty)"
        names = [str(v.get("name", v)) if isinstance(v, dict) else str(v) for v in value]
        head = ", ".join(names[:5])
        return f"{len(names)}: {head}" + (" ..." if len(names) > 5 else "")
    return str(value)


def check_isolation(init_info: dict | None) -> list[IsolationCheck]:
    """세션 시작 정보로 격리를 판정한다. tools·mcp_servers·plugins 는 빈 목록, model 에 haiku, output_style 은 없거나 default."""
    if init_info is None:
        return [IsolationCheck(k, "(no init)", False)
                for k in ("tools", "mcp_servers", "plugins", "model", "output_style")]
    checks = [IsolationCheck(k, _summary(init_info.get(k)), init_info.get(k) == [])
              for k in ("tools", "mcp_servers", "plugins")]
    model = init_info.get("model")
    checks.append(IsolationCheck("model", _summary(model), isinstance(model, str) and "haiku" in model.lower()))
    style = init_info.get("output_style")
    checks.append(IsolationCheck("output_style", "(none)" if style is None else str(style),
                                 style is None or style == "default"))
    return checks


def isolation_ok(checks: list[IsolationCheck]) -> bool:
    return all(c.ok for c in checks)


# --- 요금 지킴이 ------------------------------------------------------------------------------
# 사용자의 절대 규칙: API 요금 0원, 구독만 쓴다. init 의 apiKeySource 는 자격증명이 어디서 왔는지 알려 준다
# ("none" = API 키 없음, 구독 로그인/OAuth 토큰). 그 밖의 값("ANTHROPIC_API_KEY", "apiKeyHelper",
# "/login managed key")이거나 값이 없으면 API 키로 과금될 수 있으니 세션을 거부한다(모르면 막는다).
# init 은 턴의 응답 맨 앞에 오므로 첫 턴의 요청은 이미 나간 뒤다. 그래서 보자마자 interrupt 로 답 생성을 멈춘다.

def billing_ok(init_info: dict | None) -> bool:
    """세션 시작 정보가 구독 인증(apiKeySource == "none")을 가리키면 True."""
    return isinstance(init_info, dict) and init_info.get("apiKeySource") == "none"


def _billing_refusal(init_info: dict) -> str:
    source = str(init_info.get("apiKeySource", "(missing)"))[:40]  # 출처 이름일 뿐 비밀 값이 아니다
    return f"refused: apiKeySource={source!r} could bill an API key (subscription only)"


# --- 액터 -------------------------------------------------------------------------------------

_STOP = object()


@dataclass(eq=False)
class _Turn:
    message: str
    future: asyncio.Future | None = None
    abandoned: bool = False  # 부른 쪽이 시간 초과로 포기했다


class _TurnFailed(Exception):
    """이 턴만 실패했다(예: API 오류 결과). 세션은 계속 쓸 수 있다."""


class _SessionRefused(Exception):
    """이 세션을 더 쓰면 안 된다(예: API 키 과금 경로). 액터가 세션을 끝낸다."""


def _settle(future: asyncio.Future, *, result: Any = None, error: BaseException | None = None) -> None:
    if future.done():  # 포기한 턴(취소됨)이면 결과를 버린다
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


def _schedule(loop: asyncio.AbstractEventLoop, coro: Any) -> concurrent.futures.Future | None:
    """다른 스레드에서 액터 루프에 코루틴을 넣는다. 루프가 이미 닫혔으면 코루틴을 닫고 None."""
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError:
        coro.close()  # "never awaited" 경고 없이 버린다
        return None


def _describe_error_result(result: ResultMessage, assistant_error: str | None) -> str:
    """오류 결과를 한 줄로. 로그인 안 됨 같은 이유는 CLI 가 result 글과 assistant error 로만 알려 준다."""
    reason = " ".join(str(result.result or "").split())[:200]
    detail = (f"reason={reason!r}, assistant_error={assistant_error}, subtype={result.subtype}, "
              f"api_error_status={result.api_error_status}, errors={result.errors}")
    return f"claude returned an error result ({detail[:400]})"


class _Session:
    """세션 하나 = 스레드 하나 + 이벤트 루프 하나 + ClaudeSDKClient 하나. 액터만 이 상태를 바꾼다."""

    def __init__(self, options: ClaudeAgentOptions, on_refused: Callable[[str], None] | None = None):
        self.options = options
        self._on_refused = on_refused  # 요금 지킴이가 거부하면 액터 스레드에서 부른다(이유 한 줄)
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        options.stderr = self.stderr_tail.append  # CLI 의 stderr 는 서버 콘솔 대신 여기에 최근 줄만 남긴다
        self.loop = asyncio.new_event_loop()
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.ready = threading.Event()  # 연결이 끝났거나 실패했다
        self.client: Any = None
        self.current: _Turn | None = None
        self.failure: str | None = None  # 값이 있으면 세션이 끝났다(이유)
        self.refused: str | None = None  # 요금 지킴이가 거부한 이유
        self.init_info: dict | None = None
        self.connect_ms: int | None = None
        self._background: set[asyncio.Future] = set()
        self.thread = threading.Thread(target=self._run, name="claude-agent-llm", daemon=True)

    # 스레드 안 -----------------------------------------------------------------------------
    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._actor())
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            finally:
                self.loop.close()

    async def _actor(self) -> None:
        started = time.perf_counter()
        try:
            async with ClaudeSDKClient(self.options) as client:
                self.client = client
                self.connect_ms = int((time.perf_counter() - started) * 1000)
                self.ready.set()
                while True:
                    turn = await self.inbox.get()
                    if turn is _STOP:
                        break
                    if turn.abandoned or turn.future.done():
                        continue  # 기다리다 포기한 턴은 보내지도 않는다
                    self.current = turn
                    try:
                        text, model = await self._run_turn(client, turn.message)
                    except _SessionRefused as e:
                        # 먼저 적어 둔다: 부른 쪽이 이 턴의 답을 받기 전에 객체 전체가 거부 상태가 된다
                        self.refused = self.failure = str(e)
                        if self._on_refused is not None:
                            self._on_refused(self.refused)
                        _settle(turn.future, error=LLMBillingRefused(self.refused))
                        break  # async with 를 빠져나가 claude.exe 를 닫는다
                    except _TurnFailed as e:
                        _settle(turn.future, error=LLMError(str(e)))
                    except Exception as e:
                        _settle(turn.future, error=LLMError(f"claude session died: {type(e).__name__}: {e}"))
                        raise
                    else:
                        _settle(turn.future, result=(text, model))
                    finally:
                        self.current = None
        except Exception as e:
            self.failure = f"{type(e).__name__}: {e}"
        finally:
            self.client = None
            if self.failure is None:
                self.failure = "session ended"
            self.ready.set()
            while not self.inbox.empty():  # 아직 줄 서 있던 턴은 바로 실패로 알린다
                turn = self.inbox.get_nowait()
                if turn is not _STOP:
                    _settle(turn.future, error=self.not_running())

    async def _run_turn(self, client: Any, message: str) -> tuple[str, str | None]:
        await client.query(message)
        parts: list[str] = []
        model: str | None = None
        assistant_error: str | None = None
        result: ResultMessage | None = None
        refusal: str | None = None
        async for m in client.receive_response():  # ResultMessage 를 내고 스스로 끝난다. break 금지.
            if isinstance(m, SystemMessage):
                if m.subtype == "init":
                    data = dict(m.data)
                    if self.init_info is None:
                        self.init_info = data
                    if refusal is None and not billing_ok(data):  # 턴마다 오는 init 을 모두 본다
                        refusal = _billing_refusal(data)
                        await self._interrupt(client)  # 답 생성을 바로 멈추고, 끝(ResultMessage)까지는 읽는다
            elif isinstance(m, AssistantMessage):
                model = m.model or model
                assistant_error = m.error or assistant_error
                parts.extend(b.text for b in m.content if isinstance(b, TextBlock))
            elif isinstance(m, ResultMessage):
                result = m
        if refusal is not None:
            raise _SessionRefused(refusal)
        if result is None:
            raise LLMError("claude stream ended without a result")
        if result.is_error:
            raise _TurnFailed(_describe_error_result(result, assistant_error))
        return "".join(parts) or (result.result or ""), model

    def not_running(self) -> LLMError:
        if self.refused is not None:
            return LLMBillingRefused(self.refused)
        return LLMError(f"claude session is not running ({self.failure})")

    async def submit(self, turn: _Turn) -> tuple[str, str | None]:
        if self.failure is not None:
            raise self.not_running()
        turn.future = self.loop.create_future()
        self.inbox.put_nowait(turn)
        return await turn.future

    async def abandon(self, turn: _Turn) -> None:
        turn.abandoned = True
        if self.current is turn and self.client is not None:
            await self._interrupt(self.client)

    async def _interrupt(self, client: Any) -> None:
        try:
            await client.interrupt()
        except Exception as e:  # 인터럽트가 실패해도 턴은 언젠가 끝난다. 기록만 남긴다.
            self.stderr_tail.append(f"[kids-english] interrupt failed: {type(e).__name__}: {e}")

    async def shutdown(self) -> None:
        if self.current is not None:  # 진행 중인 턴은 interrupt 로 빨리 끝내게 한다(기다리지 않음)
            task = asyncio.ensure_future(self.abandon(self.current))
            self._background.add(task)  # 루프는 태스크를 약하게만 잡으므로 끝날 때까지 참조를 쥔다
            task.add_done_callback(self._background.discard)
        self.inbox.put_nowait(_STOP)


class ClaudeAgentLLM(LLM):
    """`LLM` 구현. `start_session` 이 claude.exe 를 미리 띄우고, `complete` 가 턴을 하나씩 보낸다."""

    def __init__(self, model: str = "haiku", workdir: Path | None = None, oauth_token: str | None = None):
        self.model = model
        self.workdir = Path(workdir) if workdir is not None else default_workdir()
        self.__oauth_token = oauth_token  # repr 에도 로그에도 나오지 않는다
        self._session: _Session | None = None
        self._refused: str | None = None  # 한 번 정해지면 이 객체가 사는 동안 그대로다

    def __repr__(self) -> str:
        return f"ClaudeAgentLLM(model={self.model!r}, workdir={str(self.workdir)!r})"

    @property
    def init_info(self) -> dict | None:
        """세션 첫 턴의 `SystemMessage(subtype="init").data`. 첫 답이 오기 전에는 None."""
        return self._session.init_info if self._session else None

    @property
    def refused(self) -> str | None:
        """요금 지킴이가 거부한 이유(None = 거부한 적 없음). 한 번 정해지면 이 객체가 사는 동안 바뀌지 않는다."""
        return self._refused

    def _refuse(self, reason: str) -> None:
        if self._refused is None:
            self._refused = reason

    @property
    def connect_ms(self) -> int | None:
        """claude.exe 를 띄우고 초기화가 끝날 때까지 걸린 시간."""
        return self._session.connect_ms if self._session else None

    @property
    def stderr_tail(self) -> list[str]:
        """CLI stderr 의 최근 줄(진단용)."""
        return list(self._session.stderr_tail) if self._session else []

    def start_session(self, system_prompt: str) -> None:
        """시스템 프롬프트를 작업 폴더에 쓰고 claude.exe 를 띄우기 시작한 뒤 바로 돌아온다(연결은 기다리지 않음).

        요금 지킴이가 이미 거부한 객체면 아무것도 띄우지 않는다(연결도 질의도 0). 그때 `complete` 는 `LLMBillingRefused`.
        """
        self.end_session()
        if self._refused is not None:
            return
        self.workdir.mkdir(parents=True, exist_ok=True)
        prompt_path = self.workdir / SYSTEM_PROMPT_FILE
        prompt_path.write_text(system_prompt, encoding="utf-8", newline="\n")
        session = _Session(build_options(self.model, prompt_path, self.workdir, self.__oauth_token),
                           on_refused=self._refuse)
        self._session = session
        session.thread.start()

    def wait_ready(self, timeout_s: float) -> bool:
        """연결이 끝날 때까지 최대 timeout_s 초 기다린다. 연결돼 있으면 True."""
        s = self._session
        if s is None:
            return False
        s.ready.wait(timeout_s)
        return s.ready.is_set() and s.client is not None and s.failure is None

    def complete(self, user_msg: str, timeout_s: float) -> LLMResult:
        if self._refused is not None:  # 질의를 보내지 않는다
            raise LLMBillingRefused(self._refused)
        s = self._session
        if s is None:
            raise LLMError("no claude session: call start_session first")
        if s.failure is not None:
            raise s.not_running()
        started = time.perf_counter()
        turn = _Turn(user_msg)
        pending = _schedule(s.loop, s.submit(turn))
        if pending is None:
            raise LLMError("claude session is not running (event loop closed)")
        try:
            text, model = pending.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            pending.cancel()
            _schedule(s.loop, s.abandon(turn))  # 진행 중인 턴이면 interrupt 를 보낸다
            raise LLMTimeout(f"no answer within {timeout_s}s") from None
        except LLMError:
            raise
        except concurrent.futures.CancelledError as e:
            raise LLMError("claude session closed during the turn") from e
        except Exception as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        return LLMResult(text=text, latency_ms=int((time.perf_counter() - started) * 1000), model=model)

    def end_session(self) -> None:
        """종료 신호를 넣고 스레드를 최대 5초 기다린다. 두 번 불러도 안전하다. `init_info` 는 남겨 둔다."""
        s = self._session
        if s is None or not s.thread.is_alive():
            return
        if s.failure is None:  # 액터가 아직 돈다(이미 끝났으면 스레드가 정리 중이니 기다리기만 한다)
            _schedule(s.loop, s.shutdown())
        s.thread.join(5.0)


__all__ = [
    "ISOLATION_ENV",
    "ClaudeAgentLLM",
    "IsolationCheck",
    "billing_ok",
    "build_options",
    "check_isolation",
    "default_workdir",
    "isolation_ok",
]
