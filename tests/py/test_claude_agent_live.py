"""실제 Claude(구독, haiku)를 부르는 테스트. 기본 실행에서 빠진다(pytest.ini 의 -m "not live").

실행(사용자 승인 후): .venv/Scripts/python -m pytest -m live -q   — 쿼터를 3턴쯤 쓴다.
"""
import json
import os
from pathlib import Path

import pytest

from server.config import load_config, scrub_llm_env
from server.llm import LLMTimeout
from server.llm.claude_agent import ClaudeAgentLLM, check_isolation, isolation_ok

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[2]


def _prompt() -> str:
    template = (ROOT / "prompts" / "chomp_system.md").read_text(encoding="utf-8")
    return template.replace("{VOCAB}", "(none yet)").replace("{CATALOG}", "(none yet)")


def _turn(text: str) -> str:
    return json.dumps({"heard": [text], "scene": [], "applied_ops": [], "hint_requested": False})


@pytest.fixture
def live_llm(tmp_path, monkeypatch):
    # 서버(run.py)와 똑같이 물려받으면 안 되는 변수를 지운다. 테스트가 끝나면 monkeypatch 가 되돌린다.
    for name in scrub_llm_env(dict(os.environ)):
        monkeypatch.delenv(name)
    llm = ClaudeAgentLLM("haiku", workdir=tmp_path, oauth_token=load_config(ROOT, []).oauth_token)
    llm.start_session(_prompt())
    yield llm
    llm.end_session()


def test_one_live_turn_answers_and_is_isolated(live_llm):
    r = live_llm.complete(_turn("hello chomp"), timeout_s=60)
    assert r.text.strip()
    assert live_llm.init_info["tools"] == []
    assert isolation_ok(check_isolation(live_llm.init_info)), check_isolation(live_llm.init_info)


def test_live_timeout_interrupts_and_the_next_turn_still_answers(live_llm):
    assert live_llm.wait_ready(60)
    with pytest.raises(LLMTimeout):
        live_llm.complete(_turn("tell me a story about a big red egg"), timeout_s=0.05)
    r = live_llm.complete(_turn("jump"), timeout_s=60)
    assert r.text.strip()
