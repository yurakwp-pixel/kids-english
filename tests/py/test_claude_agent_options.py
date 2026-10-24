"""ClaudeAgentLLM 계약 테스트. 진짜 claude.exe 를 띄우지 않는다(쿼터 사용 0).

- build_options: 아이 대화용 Claude 가 사용자 설정·도구·MCP 를 하나도 못 보게 하는 옵션 묶음.
- ClaudeAgentLLM: 가짜 ClaudeSDKClient 를 끼워 액터 스레드의 동작(턴 처리, 시간 초과, 오류, 종료)을 본다.
- check_isolation: 세션 시작 정보(init)로 격리가 실제로 됐는지 판정한다.
"""
import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock
from claude_agent_sdk import CLIConnectionError

import server.llm.claude_agent as claude_agent
from server.llm import LLM, LLMBillingRefused, LLMError, LLMTimeout
from server.llm.claude_agent import (
    ISOLATION_ENV,
    ClaudeAgentLLM,
    build_options,
    check_isolation,
    default_workdir,
    isolation_ok,
)

ROOT = Path(__file__).resolve().parents[2]


# --- build_options (brief Step 1) -------------------------------------------------------------

def test_build_options_isolates_everything(tmp_path):
    o = build_options("haiku", tmp_path / "sys.md", tmp_path, None)
    assert o.model == "haiku"
    assert o.system_prompt == {"type": "file", "path": str(tmp_path / "sys.md")}
    assert o.tools == [] and o.setting_sources == [] and o.mcp_servers == {}
    assert "mcp__*" in o.disallowed_tools and o.strict_mcp_config is True
    assert o.permission_mode == "dontAsk" and o.max_turns == 1
    assert o.thinking == {"type": "disabled"}
    assert o.extra_args == {"no-session-persistence": None, "disable-slash-commands": None}
    assert o.cwd == tmp_path
    assert ISOLATION_ENV.items() <= o.env.items()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in o.env and "ANTHROPIC_API_KEY" not in o.env
    # CLI 에 들어 있는 기본 플러그인 3개(init.plugins 에 보였던 이름)를 --settings 로 끈다.
    assert json.loads(o.settings) == {"enabledPlugins": {
        "cc-plugin-agents-md@builtin": False,
        "cc-plugin-telemetry@builtin": False,
        "cc-plugin-plugin-authoring@builtin": False,
    }}


def test_build_options_passes_token_only_when_given(tmp_path):
    o = build_options("haiku", tmp_path / "s.md", tmp_path, "tok")
    assert o.env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def test_default_workdir_is_ascii():
    assert str(default_workdir()).isascii()


def test_isolation_env_values_are_exact():
    assert ISOLATION_ENV == {
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
    }


def test_build_options_treats_empty_token_as_none(tmp_path):
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in build_options("haiku", tmp_path / "s.md", tmp_path, "").env


def test_build_options_does_not_share_env_dict_between_calls(tmp_path):
    a = build_options("haiku", tmp_path / "s.md", tmp_path, "tok")
    b = build_options("haiku", tmp_path / "s.md", tmp_path, None)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in b.env and "CLAUDE_CODE_OAUTH_TOKEN" not in ISOLATION_ENV
    assert a.env is not b.env


# --- 가짜 ClaudeSDKClient ---------------------------------------------------------------------

INIT = {"type": "system", "subtype": "init", "tools": [], "mcp_servers": [], "plugins": [],
        "model": "claude-haiku-test", "output_style": "default", "apiKeySource": "none"}


def _result(is_error=False, terminal_reason="completed"):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=1, session_id="s", terminal_reason=terminal_reason)


def reply(*texts, init=INIT):
    async def turn(client):
        yield SystemMessage(subtype="init", data=dict(init))
        for t in texts:
            yield AssistantMessage(content=[TextBlock(text=t)], model="claude-haiku-test")
        yield _result()
    return turn


def hang_until_interrupt():
    async def turn(client):
        await client.interrupted.wait()
        yield _result(terminal_reason="aborted_streaming")
    return turn


def error_result(text=None, assistant_error=None):
    async def turn(client):
        if assistant_error:
            yield AssistantMessage(content=[TextBlock(text=text or "")], model="<synthetic>",
                                   error=assistant_error)
        r = _result(is_error=True)
        r.result = text
        yield r
    return turn


def crash():
    async def turn(client):
        raise RuntimeError("cli died")
        yield  # pragma: no cover  (비동기 제너레이터로 만들기 위한 줄)
    return turn


class FakeClient:
    """ClaudeSDKClient 대역. 턴마다 `turns` 에서 하나씩 꺼내 메시지를 흘려보낸다."""

    instances: list["FakeClient"] = []
    turns: list = []
    fail_connect = False

    def __init__(self, options):
        self.options = options
        self.queries: list[str] = []
        self.interrupt_calls = 0
        self.closed = False
        self.interrupted = asyncio.Event()
        FakeClient.instances.append(self)

    async def __aenter__(self):
        if FakeClient.fail_connect:
            raise CLIConnectionError("cannot start claude")
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        turn = FakeClient.turns.pop(0)
        async for m in turn(self):
            yield m

    async def interrupt(self):
        self.interrupt_calls += 1
        self.interrupted.set()


@pytest.fixture
def fake(monkeypatch):
    FakeClient.instances = []
    FakeClient.turns = []
    FakeClient.fail_connect = False
    monkeypatch.setattr(claude_agent, "ClaudeSDKClient", FakeClient)
    return FakeClient


@pytest.fixture
def llm(tmp_path, fake):
    made = []

    def _make():
        x = ClaudeAgentLLM("haiku", workdir=tmp_path)
        made.append(x)
        return x

    yield _make
    for x in made:
        x.end_session()


def _wait_until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# --- ClaudeAgentLLM ---------------------------------------------------------------------------

def test_claude_agent_llm_is_an_llm():
    assert isinstance(ClaudeAgentLLM(), LLM)


def test_complete_collects_text_model_and_init_info(llm, fake, tmp_path):
    fake.turns = [reply("Hi ", "there!")]
    x = llm()
    x.start_session("멍멍이 프롬프트 ✓")
    r = x.complete("hello chomp", timeout_s=2)
    assert (r.text, r.model) == ("Hi there!", "claude-haiku-test")
    assert isinstance(r.latency_ms, int) and r.latency_ms >= 0
    assert x.init_info == INIT
    prompt_file = tmp_path / "system_prompt.md"
    assert prompt_file.read_text(encoding="utf-8") == "멍멍이 프롬프트 ✓"
    client = fake.instances[0]
    assert client.options.system_prompt == {"type": "file", "path": str(prompt_file)}
    assert client.options.cwd == tmp_path and client.queries == ["hello chomp"]


def test_one_client_serves_the_whole_session_and_first_init_is_kept(llm, fake):
    fake.turns = [reply("one"), reply("two", init={**INIT, "model": "other"})]
    x = llm()
    x.start_session("S")
    assert [x.complete("a", 2).text, x.complete("b", 2).text] == ["one", "two"]
    assert len(fake.instances) == 1 and fake.instances[0].queries == ["a", "b"]
    assert x.init_info["model"] == "claude-haiku-test"


def test_wait_ready_reports_a_connected_session(llm, fake):
    fake.turns = [reply("ok")]
    x = llm()
    x.start_session("S")
    assert x.wait_ready(2) is True
    assert x.complete("a", 2).text == "ok"


def test_timeout_interrupts_the_turn_and_the_session_survives(llm, fake):
    fake.turns = [hang_until_interrupt(), reply("fresh")]
    x = llm()
    x.start_session("S")
    with pytest.raises(LLMTimeout):
        x.complete("slow", timeout_s=0.2)
    assert _wait_until(lambda: fake.instances[0].interrupt_calls == 1)
    # 끊긴 턴의 끝(ResultMessage)까지 읽었으므로 다음 턴은 제 답을 받는다.
    assert x.complete("next", timeout_s=2).text == "fresh"
    assert fake.instances[0].queries == ["slow", "next"]


def test_turn_abandoned_while_waiting_in_queue_is_never_sent(llm, fake):
    gate = threading.Event()

    async def slow_turn(client):
        while not gate.is_set():
            await asyncio.sleep(0.01)
        yield _result()

    fake.turns = [slow_turn, reply("third")]
    x = llm()
    x.start_session("S")
    first = threading.Thread(target=lambda: x.complete("first", timeout_s=5))
    first.start()
    assert _wait_until(lambda: fake.instances and fake.instances[0].queries == ["first"])
    with pytest.raises(LLMTimeout):
        x.complete("second", timeout_s=0.1)  # 첫 턴이 끝나기 전에 포기한 요청
    gate.set()
    first.join(2)
    assert x.complete("third", timeout_s=2).text == "third"
    assert fake.instances[0].queries == ["first", "third"]


def test_error_result_raises_llm_error_but_session_survives(llm, fake):
    fake.turns = [error_result(), reply("ok")]
    x = llm()
    x.start_session("S")
    with pytest.raises(LLMError) as e:
        x.complete("a", 2)
    assert not isinstance(e.value, LLMTimeout)
    assert x.complete("b", 2).text == "ok"


def test_error_result_message_carries_the_cli_reason(llm, fake):
    # 로그인 안 됨 같은 오류는 CLI 가 result 글과 assistant error 로만 알려 준다.
    fake.turns = [error_result("Not logged in · Please run /login", assistant_error="authentication_failed")]
    x = llm()
    x.start_session("S")
    with pytest.raises(LLMError) as e:
        x.complete("a", 2)
    assert "Not logged in" in str(e.value) and "authentication_failed" in str(e.value)


def test_connect_failure_raises_llm_error_quickly(llm, fake):
    fake.fail_connect = True
    x = llm()
    x.start_session("S")
    started = time.monotonic()
    with pytest.raises(LLMError) as e:
        x.complete("a", timeout_s=5)
    assert not isinstance(e.value, LLMTimeout) and time.monotonic() - started < 2
    assert x.wait_ready(1) is False


def test_crash_mid_turn_kills_the_session(llm, fake):
    fake.turns = [crash(), reply("never")]
    x = llm()
    x.start_session("S")
    with pytest.raises(LLMError):
        x.complete("a", 2)
    with pytest.raises(LLMError):
        x.complete("b", 2)
    assert fake.instances[0].queries == ["a"] and fake.instances[0].closed


def test_complete_without_session_raises_llm_error(tmp_path, fake):
    with pytest.raises(LLMError):
        ClaudeAgentLLM(workdir=tmp_path).complete("a", 1)


def test_end_session_is_idempotent_and_closes_the_client(llm, fake):
    fake.turns = [reply("ok")]
    x = llm()
    x.start_session("S")
    x.complete("a", 2)
    x.end_session()
    x.end_session()
    assert fake.instances[0].closed
    with pytest.raises(LLMError):
        x.complete("b", 1)


def test_start_session_again_replaces_the_old_client(llm, fake):
    fake.turns = [reply("one"), reply("two")]
    x = llm()
    x.start_session("S1")
    x.complete("a", 2)
    x.start_session("S2")
    assert x.complete("b", 2).text == "two"
    assert len(fake.instances) == 2 and fake.instances[0].closed and not fake.instances[1].closed
    assert x.init_info == INIT


@pytest.mark.parametrize("source", ["ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key", "", None])
def test_session_billed_to_an_api_key_is_refused(llm, fake, source):
    # 사용자의 절대 규칙: API 요금 0원(구독만). init 의 apiKeySource 가 "none" 이 아니면(없어도) 세션을 거부한다.
    init = {**INIT, "apiKeySource": source} if source is not None else {
        k: v for k, v in INIT.items() if k != "apiKeySource"}
    fake.turns = [reply("paid answer", init=init), reply("never")]
    x = llm()
    x.start_session("S")
    with pytest.raises(LLMBillingRefused) as e:
        x.complete("a", 2)
    assert isinstance(e.value, LLMError) and not isinstance(e.value, LLMTimeout)
    assert "apiKeySource" in str(e.value) and "paid answer" not in str(e.value)
    client = fake.instances[0]
    assert client.interrupt_calls == 1  # 답을 만들기 시작했더라도 바로 멈추게 한다
    assert _wait_until(lambda: client.closed)  # 세션(claude.exe)을 끝낸다
    with pytest.raises(LLMBillingRefused) as again:
        x.complete("b", 2)
    assert "apiKeySource" in str(again.value)
    assert client.queries == ["a"]
    assert x.refused is not None and "apiKeySource" in x.refused
    assert x.init_info["apiKeySource" if source is not None else "model"] is not None


def test_billing_refusal_is_sticky_across_new_sessions(llm, fake, tmp_path):
    # 페이지를 다시 열 때마다(/api/session/start) 유료 질의가 한 번씩 나가면 안 된다: 거부는 이 객체가 사는 동안 유지된다.
    fake.turns = [reply("paid", init={**INIT, "apiKeySource": "ANTHROPIC_API_KEY"}), reply("never")]
    x = llm()
    assert x.refused is None
    x.start_session("S1")
    with pytest.raises(LLMBillingRefused):
        x.complete("a", 2)
    first = fake.instances[0]
    assert _wait_until(lambda: first.closed)
    (tmp_path / "system_prompt.md").unlink()
    for prompt in ("S2", "S3"):
        x.start_session(prompt)  # claude.exe 를 다시 띄우지 않는다
        assert x.wait_ready(0.2) is False
        with pytest.raises(LLMBillingRefused) as e:
            x.complete("b", 2)
        assert "apiKeySource" in str(e.value)
    assert len(fake.instances) == 1  # 새 연결 없음
    assert first.queries == ["a"]  # 거부 뒤로는 질의 0개
    assert not (tmp_path / "system_prompt.md").exists()  # 작업 폴더도 건드리지 않는다


def test_refusal_does_not_leak_into_a_new_instance(llm, fake):
    fake.turns = [reply("paid", init={**INIT, "apiKeySource": "apiKeyHelper"}), reply("ok")]
    refused = llm()
    refused.start_session("S")
    with pytest.raises(LLMBillingRefused):
        refused.complete("a", 2)
    fresh = llm()  # 서버를 다시 켠 것과 같다: 새 객체는 다시 init 으로 판정한다
    fresh.start_session("S")
    assert fresh.complete("b", 2).text == "ok" and fresh.refused is None


def test_billing_ok_accepts_only_none():
    assert claude_agent.billing_ok({"apiKeySource": "none"})
    for bad in [{"apiKeySource": "ANTHROPIC_API_KEY"}, {"apiKeySource": None}, {}, None]:
        assert not claude_agent.billing_ok(bad)


def test_repr_never_shows_the_token(tmp_path):
    x = ClaudeAgentLLM(workdir=tmp_path, oauth_token="super-secret-value")
    assert "super-secret-value" not in repr(x) and "super-secret-value" not in str(x)


# --- check_isolation --------------------------------------------------------------------------

def test_check_isolation_passes_a_clean_init():
    checks = check_isolation(INIT)
    assert isolation_ok(checks) and [c.key for c in checks] == [
        "tools", "mcp_servers", "plugins", "model", "output_style"]


def test_check_isolation_allows_missing_output_style():
    init = {k: v for k, v in INIT.items() if k != "output_style"}
    assert isolation_ok(check_isolation(init))


@pytest.mark.parametrize("leak", [
    {"tools": ["Bash"]},
    {"mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}]},
    {"plugins": [{"name": "superpowers", "path": "x"}]},
    {"model": "claude-sonnet-test"},
    {"output_style": "Explanatory"},
    {"tools": None},
])
def test_check_isolation_flags_each_leak(leak):
    checks = check_isolation({**INIT, **leak})
    bad = [c.key for c in checks if not c.ok]
    assert bad == list(leak) and not isolation_ok(checks)


def test_check_isolation_fails_without_init():
    checks = check_isolation(None)
    assert not isolation_ok(checks) and all(not c.ok for c in checks)


# --- 시스템 프롬프트 템플릿 ------------------------------------------------------------------

def test_chomp_prompt_template_has_placeholders():
    text = (ROOT / "prompts" / "chomp_system.md").read_text(encoding="utf-8")
    assert text.count("{VOCAB}") == 1 and text.count("{CATALOG}") == 1
    assert '"say"' in text and "Your turn!" in text
