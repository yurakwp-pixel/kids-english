"""LLM 어댑터 인터페이스.

서버(턴 처리)는 이 Protocol 만 안다. 진짜 Claude(ClaudeAgentLLM, Task 4)와 테스트용 FakeLLM 이
같은 모양이라서, 턴 처리 코드는 어느 쪽이 꽂혀 있는지 모른 채 돌아간다.

규칙:
- `complete` 는 시간이 넘으면 `LLMTimeout`, 그 밖의 실패는 `LLMError` 를 던진다.
- 실패해도 놀이는 멈추지 않는다. 호출하는 쪽이 `canned.fallback_reply` 로 대신 답한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class LLMResult:
    """LLM 한 번 호출의 결과. `text` 는 모델이 낸 글 그대로(검증은 validator 가 한다)."""

    text: str
    latency_ms: int
    model: str | None = None


class LLMError(Exception):
    """LLM 호출 실패의 뿌리. 연결, 인증, 프로세스 오류 등."""


class LLMTimeout(LLMError):
    """정해진 시간 안에 답이 오지 않았다."""


@runtime_checkable
class LLM(Protocol):
    def start_session(self, system_prompt: str) -> None:
        """세션 하나를 시작한다. `system_prompt` 가 대화 내내 쓰인다."""
        ...

    def complete(self, user_msg: str, timeout_s: float) -> LLMResult:
        """사용자 메시지 하나를 보내 답을 받는다. 시간 초과는 `LLMTimeout`, 실패는 `LLMError`."""
        ...

    def end_session(self) -> None:
        """세션을 끝내고 자원을 정리한다. 두 번 불러도 안전해야 한다."""
        ...


__all__ = ["LLM", "LLMResult", "LLMError", "LLMTimeout"]
