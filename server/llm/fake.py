"""테스트와 `--fake-llm` 용 가짜 LLM. 돈도 네트워크도 쓰지 않는다.

`script` 에 미리 적어 둔 답을 차례로 돌려주고, 다 쓰면 마지막 항목을 계속 반복한다.
항목이 예외(LLMError 등)이면 돌려주는 대신 던진다 — 실패 상황을 재현하는 용도.
"""
from __future__ import annotations

import threading
import time

from server.llm import LLMResult, LLMTimeout

DEFAULT_SCRIPT: list[str | BaseException] = ["{}"]


class FakeLLM:
    def __init__(self, script: list[str | BaseException] | None = None, delay_s: float = 0.0):
        self._script: list[str | BaseException] = list(script) if script else list(DEFAULT_SCRIPT)
        self.delay_s = delay_s
        self.system_prompt: str | None = None
        self.calls: list[str] = []
        # 동시에 complete 안에 있었던 호출 수의 최댓값. 턴 처리기가 턴을 하나씩 보내는지 보는 계기다.
        self.max_concurrent = 0
        self._active = 0
        self._next = 0
        self._lock = threading.Lock()

    def start_session(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt

    def complete(self, user_msg: str, timeout_s: float) -> LLMResult:
        started = time.perf_counter()
        with self._lock:
            self.calls.append(user_msg)
            item = self._script[min(self._next, len(self._script) - 1)]
            self._next += 1
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            if self.delay_s > timeout_s:
                time.sleep(max(timeout_s, 0.0))  # 진짜 LLM 처럼 제한 시간만큼 기다렸다가 포기한다
                raise LLMTimeout(f"no answer within {timeout_s}s")
            if self.delay_s > 0:
                time.sleep(self.delay_s)
            if isinstance(item, BaseException):
                raise item
            return LLMResult(text=item, latency_ms=int((time.perf_counter() - started) * 1000))
        finally:
            with self._lock:
                self._active -= 1

    def end_session(self) -> None:
        """할 일 없음. `system_prompt` 와 `calls` 는 테스트가 끝난 뒤에도 살펴볼 수 있게 남겨 둔다."""
