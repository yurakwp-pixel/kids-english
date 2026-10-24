"""턴 처리기: 아이의 한마디(턴 요청 하나)를 NDJSON 두 줄로 바꾼다. HTTP 는 모르고, 줄(dict)만 내보낸다.

흐름 (M1):
  1. 요청 검사. session_id·turn_id 가 틀리면 `BadRequest`(첫 줄을 내기 전에). 인식 후보는 앞 5개만, 공백·서로게이트·길이를
     정리하고, 비었거나 군소리("uh", "um")뿐인 후보는 빼고, `redact` 로 가린다. 이 뒤로 LLM 과 로그에는 가린 글자만 간다.
  2. 잠금(`threading.Lock`): 한 번에 한 턴만 처리한다. 아이는 한 명이고 SDK 액터도 한 줄로 받는다(Review Focus 2).
     두 번째 턴은 첫 턴이 끝날 때까지 기다린다.
  3. 1줄 `{"kind":"ack","turn_id":N}` 을 LLM 을 부르기 전에 내보낸다(M2 에서 fastpath 가 `fast` 를 더한다).
  4. 지금 세션이 아닌 session_id 면 그 세션을 연다(implicit start). 서버가 다시 켜져도 열려 있던 아이 화면의 다음 턴이
     400 없이 그대로 이어진다. 결과는 "가장 최근 session_id 가 이긴다"이다(탭 두 개를 번갈아 쓰면 세션이 번갈아 열린다).
  5. 답:
     - 들린 말이 없음 → `fallback_reply("unclear")`, source "template", LLM 을 부르지 않는다(Review Focus 3)
     - LLM 시간 초과 / 실패 → `fallback_reply("timeout" | "error")`, source "fallback"
     - LLM 답 → `parse_reply_text` + `validate_reply`. 첫 후보에 한글이 있으면 ops 를 비운다(전역 제약).
       JSON 을 찾지 못한 답은 canned 로 바뀌므로 source "fallback". say 만 canned 로 바뀐 답은 그대로 "llm".
  6. 2줄 `{"kind":"reply","turn_id":N,"source":..., **reply, "latency":{"llm_ms":int|None,"total_ms":int}}`,
     세션 로그에 "turn" 한 줄. 어떤 실패에도 2줄은 나간다. 놀이는 멈추지 않는다.

개인정보: 로그의 turn 줄에는 turn_id, heard, source, say, ops, latency 와, 있을 때만 error(예외 클래스 이름)·issues(검증기가
고친 내역)를 쓴다. LLM 오류 메시지 글은 쓰지 않는다(아이 말이 섞일 수 있다). 오디오와 음성인식 엔진 정보는 쓰지 않는다.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator

from server.canned import fallback_reply
from server.llm import LLM, LLMBillingRefused, LLMTimeout
from server.prompt_builder import NONE_YET, build_system_prompt, build_turn_message
from server.reply import parse_reply_text, validate_reply
from server.session_log import SessionLog, valid_session_id
from server.text import clean_text, has_hangul, is_noise

MAX_ALTERNATIVES = 5   # 브라우저의 maxAlternatives 와 같다
MAX_HEARD_CHARS = 200  # 후보 하나의 글자 수 상한(한 번에 말하는 길이로 넉넉하다)
MAX_SCENE_ITEMS = 40   # 장면 물건 최대 20개 + 등장인물(Review Focus 5)에 여유를 둔 상한


def redact(text: str) -> str:
    """개인정보 가리기 자리. 지금은 받은 그대로 돌려준다.

    M3 에서 `data/private/blocklist.txt` 의 비밀 단어(이름, 유치원, 주소)를 가리도록 바꾼다.
    LLM 에 보내는 글과 로그에 쓰는 글은 반드시 이 함수를 거친다.
    """
    return text


class BadRequest(ValueError):
    """요청 모양이 틀렸다. 메시지는 짧은 코드(bad_request, bad_session_id, bad_turn_id)라 응답에 그대로 쓴다."""


@dataclass
class _Turn:
    session_id: str
    turn_id: int
    heard: list[str]  # 정리하고 가린 인식 후보. 비어 있으면 아무 말도 들리지 않은 것이다.
    scene: list[dict]
    hint_requested: bool

    def as_request(self) -> dict:
        """`build_turn_message` 가 읽는 턴 요청 모양(정리된 값만)."""
        return {"asr": {"alternatives": [{"t": t} for t in self.heard]},
                "scene": self.scene, "hint_requested": self.hint_requested}


@dataclass
class _Answer:
    reply: dict
    source: str  # "llm" | "template" | "fallback"
    llm_ms: int | None = None
    error: str | None = None  # 예외 클래스 이름만
    issues: list[str] = field(default_factory=list)


def _check_session_id(session_id: object) -> str:
    if not valid_session_id(session_id):
        raise BadRequest("bad_session_id")
    return session_id  # type: ignore[return-value]


def parse_turn_request(req: object) -> _Turn:
    """스펙 5.5 턴 요청을 검사하고 정리한다. 모르는 키(asr.engine 등)는 버린다."""
    if not isinstance(req, dict):
        raise BadRequest("bad_request")
    session_id = _check_session_id(req.get("session_id"))
    turn_id = req.get("turn_id")
    if not isinstance(turn_id, int) or isinstance(turn_id, bool) or turn_id < 0:
        raise BadRequest("bad_turn_id")

    asr = req.get("asr")
    alternatives = asr.get("alternatives") if isinstance(asr, dict) else None
    heard = []
    for alt in alternatives[:MAX_ALTERNATIVES] if isinstance(alternatives, list) else []:
        text = clean_text(alt.get("t"), MAX_HEARD_CHARS) if isinstance(alt, dict) else ""
        if not is_noise(text):
            heard.append(redact(text))

    scene = req.get("scene")
    items = [item for item in scene if isinstance(item, dict)][:MAX_SCENE_ITEMS] if isinstance(scene, list) else []
    return _Turn(session_id, turn_id, heard, items, req.get("hint_requested") is True)


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _warn(message: str) -> None:
    """서버 콘솔에 한 줄. 아이 말이나 오류 메시지 글은 넣지 않는다(종류와 번호만)."""
    print(f"[kids-english] {message}", file=sys.stderr, flush=True)


class TurnOrchestrator:
    def __init__(self, llm: LLM, log: SessionLog, prompt_template: str, timeout_s: float = 8.0) -> None:
        self.llm = llm
        self.log = log
        self.prompt_template = prompt_template
        self.timeout_s = timeout_s
        self._lock = threading.Lock()  # 턴, 세션 시작·끝을 하나씩
        self._session_id: str | None = None
        self._turns = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def system_prompt(self) -> str:
        return build_system_prompt(self.prompt_template, vocab=NONE_YET, catalog=NONE_YET)

    # ---------------------------------------------------------------- 세션

    def start_session(self, session_id: str) -> None:
        """새 세션을 연다(같은 id 여도 새로 연다). 지금 세션이 있으면 먼저 끝낸다. LLM 연결은 기다리지 않는다."""
        _check_session_id(session_id)
        with self._lock:
            self._start(session_id, implicit=False)

    def end_session(self, session_id: str) -> None:
        """지금 세션이면 끝낸다. 다른 id 면 아무것도 하지 않는다. 두 번 불러도 안전하다."""
        _check_session_id(session_id)
        with self._lock:
            if session_id == self._session_id:
                self._end()

    def close(self) -> None:
        """서버를 내릴 때: 열린 세션이 있으면 끝낸다."""
        with self._lock:
            if self._session_id is not None:
                self._end()

    def _start(self, session_id: str, *, implicit: bool) -> None:
        if self._session_id is not None:
            self._end()
        event: dict = {"type": "session_start", "implicit": implicit}
        try:
            self.llm.start_session(self.system_prompt())
        except Exception as e:  # 세션을 못 열어도 턴은 준비된 대사로 이어진다
            event["error"] = type(e).__name__
            _warn(f"LLM start_session failed: {type(e).__name__}")
        self._session_id, self._turns = session_id, 0
        self._log(session_id, event)

    def _end(self) -> None:
        session_id, turns = self._session_id, self._turns
        self._session_id = None
        try:
            self.llm.end_session()
        except Exception as e:
            _warn(f"LLM end_session failed: {type(e).__name__}")
        self._log(session_id, {"type": "session_end", "turns": turns})

    # ---------------------------------------------------------------- 턴

    def handle(self, req: dict) -> Iterator[dict]:
        """턴 하나 → ack 줄, reply 줄. 요청이 틀리면 첫 next() 에서 `BadRequest`."""
        started = time.perf_counter()
        turn = parse_turn_request(req)
        with self._lock:
            yield {"kind": "ack", "turn_id": turn.turn_id}
            if turn.session_id != self._session_id:
                self._start(turn.session_id, implicit=True)
            answer = self._answer(turn)
            self._turns += 1
            latency = {"llm_ms": answer.llm_ms, "total_ms": _ms_since(started)}
            self._log_turn(turn, answer, latency)
        yield {"kind": "reply", "turn_id": turn.turn_id, "source": answer.source, **answer.reply, "latency": latency}

    def _answer(self, turn: _Turn) -> _Answer:
        try:
            return self._ask(turn)
        except Exception as e:  # 파서·검증기의 버그여도 2줄은 나간다
            _warn(f"turn {turn.turn_id}: answer failed: {type(e).__name__}")
            return _Answer(fallback_reply("error", turn.turn_id), "fallback", error=type(e).__name__)

    def _ask(self, turn: _Turn) -> _Answer:
        if not turn.heard:
            return _Answer(fallback_reply("unclear", turn.turn_id), "template")
        message = build_turn_message(turn.as_request(), applied_ops=())
        llm_started = time.perf_counter()
        try:
            result = self.llm.complete(message, self.timeout_s)
        except Exception as e:  # LLMTimeout, LLMError, 어댑터의 예상 못 한 오류. 메시지 글은 버린다.
            reason = "timeout" if isinstance(e, LLMTimeout) else "error"
            if isinstance(e, LLMBillingRefused):  # 정해진 한 줄. 로그에는 클래스 이름만 남는다(아래 error)
                _warn(f"turn {turn.turn_id}: LLM refused: API-key billing (subscription only; fix auth, restart)")
            else:
                _warn(f"turn {turn.turn_id}: LLM {type(e).__name__}")
            return _Answer(fallback_reply(reason, turn.turn_id), "fallback", _ms_since(llm_started), type(e).__name__)
        obj = parse_reply_text(result.text)
        reply, issues = validate_reply(
            obj, turn_id=turn.turn_id, scene=turn.scene, input_has_hangul=has_hangul(turn.heard[0]))
        return _Answer(reply, "llm" if obj is not None else "fallback", result.latency_ms, None, issues)

    # ---------------------------------------------------------------- 로그

    def _log_turn(self, turn: _Turn, answer: _Answer, latency: dict) -> None:
        event: dict = {
            "type": "turn",
            "turn_id": turn.turn_id,
            "heard": turn.heard,  # 이미 redact 를 거쳤다
            "source": answer.source,
            "say": redact(answer.reply["say"]),  # LLM 이 아이 말을 따라 했을 수 있다
            "ops": answer.reply["ops"],
            "latency": latency,
        }
        if answer.error:
            event["error"] = answer.error
        if answer.issues:
            event["issues"] = answer.issues
        self._log(turn.session_id, event)

    def _log(self, session_id: str, event: dict) -> None:
        try:
            self.log.append(session_id, event)
        except (OSError, ValueError, TypeError) as e:  # 로그를 못 써도 놀이는 계속한다
            _warn(f"session log write failed: {type(e).__name__}")
