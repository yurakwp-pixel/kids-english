"""턴 처리기: 아이의 한마디(턴 요청 하나)를 NDJSON 두 줄로 바꾼다. HTTP 는 모르고, 줄(dict)만 내보낸다.

흐름 (M2, 스펙 5.5·5.6):
  1. 요청 검사. session_id·turn_id 가 틀리면 `BadRequest`(첫 줄을 내기 전에). 인식 후보는 앞 5개만, 공백·서로게이트·길이를
     정리하고, 비었거나 군소리("uh", "um")뿐인 후보는 빼고, `redact` 로 가린다. 이 뒤로 LLM 과 로그에는 가린 글자만 간다.
     한국어 입력인지(군소리를 뺀 첫 후보에 한글이 있나)는 가리기 **전에** 정한다: M3 의 redact 가 비밀 단어를 바꿔도 판단이 같다.
  2. 잠금(`threading.Lock`): 한 번에 한 턴만 처리한다. 아이는 한 명이고 SDK 액터도 한 줄로 받는다(Review Focus 2).
     두 번째 턴은 첫 턴이 끝날 때까지 기다린다.
  3. 1줄을 정한다(`_plan`, LLM 을 부르기 전):
     - 들린 말이 없음, 한국어 입력, 카탈로그 없음(M1) → 부화도 fastpath 도 없다.
     - 세션의 첫 영어 한마디 → `HATCH_OP` 를 ops 맨 앞에 넣는다(세션 상태 hatched). 부화는 `egg1`(처음 화면의 둥지 속 알)을
       없애므로 이 턴의 fastpath·LLM 은 egg1 이 없는 장면을 본다. 어떤 op 도 egg1 을 가리키지 않고, egg1 이라는 id 를 이 턴에
       새 물건에 다시 주지도 않는다(지운 id 를 같은 턴에 되살리면 화면이 같은 물건으로 착각한다).
       장면의 dino 가 `"hatched": true` 면(화면이 이미 부화했다) 서버 기억과 상관없이 부화하지 않고, 이 세션은 부화한 것으로
       기억한다. 서버를 다시 켜도(새 세션) 열린 화면에 hatch 를 또 보내지 않는다: 부화 뒤 새 알은 다시 egg1 을 받을 수 있어서
       (`server/ids.py`), 그 알을 둥지 알로 보고 지우면 아이의 알을 대상에서 빼 버린다. 화면의 false 는 서버 기억을 지우지 않는다.
     - 단어장과 카탈로그가 있으면 fastpath(`server/fastpath.py`).
     1줄 = ops 가 있으면 `{"kind":"fast","turn_id":N,"ops":[...],"sfx":"pop"}`, 없으면 `{"kind":"ack","turn_id":N}`.
  4. 지금 세션이 아닌 session_id 면 그 세션을 연다(implicit start). 서버가 다시 켜져도 열려 있던 아이 화면의 다음 턴이
     400 없이 그대로 이어진다. 결과는 "가장 최근 session_id 가 이긴다"이다(탭 두 개를 번갈아 쓰면 세션이 번갈아 열린다).
  5. 2줄의 답:
     - 들린 말이 없음 → `fallback_reply("unclear")`, source "template", LLM 을 부르지 않는다(Review Focus 3)
     - fastpath exact → 템플릿(say = FastMatch.say, korean_hint "뜻 → English!", ops []), source "template", LLM 없음
     - 그 밖(partial, 매치 없음, 한국어) → LLM. 턴 메시지의 applied_ops 는 1줄 ops 다. 답은 `validate_reply` 가 카탈로그,
       받은 그대로의 장면(1줄을 적용하기 전), 1줄 ops 로 검사한다: 1줄을 되풀이한 op 는 버려지고, 1줄이 지운 물건의 id 는
       다시 쓰이지 않는다. 한국어 입력이면 ops 를 비운다(전역 제약). LLM 이 보낸 hatch 는 버린다(부화는 서버만 한다).
       1줄과 2줄의 ops 를 합쳐 한 턴에 max_ops(4)개를 넘지 않게 2줄을 자른다.
     - LLM 시간 초과 / 실패 / JSON 아님 → source "fallback". partial 이면 장면은 이미 바뀌었으니 fastpath 의 바른 문장으로,
       아니면 `fallback_reply("timeout" | "error")`. say 만 canned 로 바뀐 답은 그대로 "llm".
  6. 2줄 `{"kind":"reply","turn_id":N,"source":..., **reply, "latency":{"llm_ms":int|None,"total_ms":int}}`,
     세션 로그에 "turn" 한 줄. 어떤 실패에도 2줄은 나간다. 놀이는 멈추지 않는다.

bank/catalog 가 None 이면 M1 과 똑같다(1줄은 언제나 ack, ops 는 모두 버린다).

개인정보: 로그의 turn 줄에는 turn_id, heard, source, say, ops(2줄), latency, hint_requested(아이가 (?) 를 눌렀나)와,
있을 때만 fast_ops(1줄)·fastpath(exact/partial)·error(예외 클래스 이름)·issues(검증기가 고친 내역)를 쓴다.
LLM 오류 메시지 글은 쓰지 않는다(아이 말이 섞일 수 있다).
오디오와 음성인식 엔진 정보는 쓰지 않는다.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator

from server.canned import fallback_reply, praise
from server.catalog import Catalog
from server.fastpath import FastMatch, match
from server.llm import LLM, LLMBillingRefused, LLMTimeout
from server.prompt_builder import (HATCH_ACTION, NONE_YET, build_system_prompt, build_turn_message, catalog_summary,
                                   vocab_lines, word_sentence)
from server.reply import MAX_OPS, parse_reply_text, validate_reply
from server.session_log import SessionLog, valid_session_id
from server.text import clean_text, has_hangul, is_noise
from server.words import Word, WordBank

MAX_ALTERNATIVES = 5   # 브라우저의 maxAlternatives 와 같다
MAX_HEARD_CHARS = 200  # 후보 하나의 글자 수 상한(한 번에 말하는 길이로 넉넉하다)
MAX_SCENE_ITEMS = 40   # 장면 물건 최대 20개 + 등장인물(Review Focus 5)에 여유를 둔 상한

HATCH_OP = {"op": "act", "actor": "dino", "action": HATCH_ACTION}
HATCH_EGG = "egg1"  # 처음 화면의 둥지 속 알(Task 14 initialScene). 화면은 hatch 를 받으면 이 알을 없앤다.


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
    korean: bool = False  # 군소리를 뺀 첫 후보에 한글이 있다(가리기 전에 정한다)
    hatched: bool = False  # 장면의 dino 가 "hatched": true(화면이 이미 부화했다, web/js/scene/model.js toWire)

    def as_request(self, scene: list[dict] | None = None) -> dict:
        """`build_turn_message` 가 읽는 턴 요청 모양(정리된 값만). scene 을 주면 그 장면을 보여 준다."""
        return {"asr": {"alternatives": [{"t": t} for t in self.heard]},
                "scene": self.scene if scene is None else scene, "hint_requested": self.hint_requested}


@dataclass
class _Plan:
    """LLM 을 부르기 전에 서버가 정한 것: 1줄 ops 와 fastpath 결과, LLM 에 보여 줄 장면."""

    scene: list[dict]  # LLM 에 보여 줄 장면(부화 턴이면 egg1 이 없다)
    hatch: bool = False
    fast: FastMatch | None = None
    ops: list[dict] = field(default_factory=list)  # 1줄로 보내는 ops. 비어 있으면 1줄은 ack

    def first_line(self, turn_id: int) -> dict:
        if self.ops:
            return {"kind": "fast", "turn_id": turn_id, "ops": self.ops, "sfx": "pop"}
        return {"kind": "ack", "turn_id": turn_id}

    def applied_ops(self) -> list[dict]:
        """검증기에 줄 '이미 적용한 ops': 1줄 ops 에 hatch 가 하는 일(egg1 지우기)을 적어 넣는다.

        검증기는 받은 그대로의 장면(egg1 이 있다)과 이 목록을 차례로 본다. 그래서 egg1 은 대상이 될 수 없고(지워졌다),
        그 id 는 이미 쓰인 것이라 LLM 의 새 물건에 다시 주지 않는다.
        """
        if not self.hatch:
            return list(self.ops)
        return [self.ops[0], {"op": "remove", "target": HATCH_EGG}, *self.ops[1:]]


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
    heard: list[str] = []
    korean = False
    for alt in alternatives[:MAX_ALTERNATIVES] if isinstance(alternatives, list) else []:
        text = clean_text(alt.get("t"), MAX_HEARD_CHARS) if isinstance(alt, dict) else ""
        if not is_noise(text):
            if not heard:
                korean = has_hangul(text)  # redact 전의 글자로 본다
            heard.append(redact(text))

    scene = req.get("scene")
    items = [item for item in scene if isinstance(item, dict)][:MAX_SCENE_ITEMS] if isinstance(scene, list) else []
    hatched = any(item.get("id") == "dino" and item.get("hatched") is True for item in items)
    return _Turn(session_id, turn_id, heard, items, req.get("hint_requested") is True, korean, hatched)


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _warn(message: str) -> None:
    """서버 콘솔에 한 줄. 아이 말이나 오류 메시지 글은 넣지 않는다(종류와 번호만)."""
    print(f"[kids-english] {message}", file=sys.stderr, flush=True)


def _note(issues: list[str], name: str) -> None:
    if name not in issues:
        issues.append(name)


def _is_hatch(op: dict) -> bool:
    return op.get("op") == "act" and op.get("action") == HATCH_ACTION


def _exclaim(text: str) -> str:
    return text.rstrip(".!? ") + "!"


class TurnOrchestrator:
    def __init__(self, llm: LLM, log: SessionLog, prompt_template: str, *, bank: WordBank | None = None,
                 catalog: Catalog | None = None, level: int = 2, timeout_s: float = 8.0) -> None:
        self.llm = llm
        self.log = log
        self.prompt_template = prompt_template
        self.bank = bank
        self.catalog = catalog
        self.level = level
        self.timeout_s = timeout_s
        self._words: dict[str, Word] = {word.id: word for word in bank.words} if bank is not None else {}
        # 한 턴(1줄 + 2줄)의 ops 한도: 스키마의 4개와 카탈로그의 max_ops 중 작은 것
        self._max_ops = min(MAX_OPS, catalog.max_ops) if catalog is not None else MAX_OPS
        self._lock = threading.Lock()  # 턴, 세션 시작·끝을 하나씩
        self._session_id: str | None = None
        self._turns = 0
        self._hatched = False  # 이 세션에서 알이 깨졌나

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def system_prompt(self) -> str:
        vocab = vocab_lines(self.bank, self.level) if self.bank is not None else NONE_YET
        catalog = catalog_summary(self.catalog) if self.catalog is not None else NONE_YET
        return build_system_prompt(self.prompt_template, vocab=vocab, catalog=catalog)

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
        self._session_id, self._turns, self._hatched = session_id, 0, False
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
        """턴 하나 → 1줄(ack 또는 fast), reply 줄. 요청이 틀리면 첫 next() 에서 `BadRequest`."""
        started = time.perf_counter()
        turn = parse_turn_request(req)
        with self._lock:
            new_session = turn.session_id != self._session_id
            # 화면이 "이미 깨어났다"고 하면 믿는다(서버를 다시 켠 뒤). 서버 기억은 같은 세션 안에서만 쓴다.
            plan = self._plan(turn, hatched=turn.hatched or (self._hatched and not new_session))
            yield plan.first_line(turn.turn_id)
            # 여기부터는 1줄이 나간 뒤다(쓰기에 실패하면 app 이 close 해서 여기에 오지 않는다)
            if new_session:
                self._start(turn.session_id, implicit=True)
            if plan.hatch or turn.hatched:
                self._hatched = True
            answer = self._answer(turn, plan)
            self._turns += 1
            latency = {"llm_ms": answer.llm_ms, "total_ms": _ms_since(started)}
            self._log_turn(turn, plan, answer, latency)
        yield {"kind": "reply", "turn_id": turn.turn_id, "source": answer.source, **answer.reply, "latency": latency}

    def _plan(self, turn: _Turn, *, hatched: bool) -> _Plan:
        if not turn.heard or turn.korean or self.catalog is None:
            return _Plan(turn.scene)
        hatch = not hatched and HATCH_ACTION in self.catalog.actor_actions("dino")
        scene = [item for item in turn.scene if item.get("id") != HATCH_EGG] if hatch else turn.scene
        # 부화 턴: id 만 있는 자리(종류 없음)라 fastpath 가 대상으로 고르지 않지만, next_ids 는 egg1 을 쓰인 id 로 센다
        fast = self._fastpath(turn, [*scene, {"id": HATCH_EGG}] if hatch else scene)
        ops = ([dict(HATCH_OP)] if hatch else []) + (list(fast.ops) if fast is not None else [])
        return _Plan(scene, hatch, fast, ops[:self._max_ops])

    def _fastpath(self, turn: _Turn, scene: list[dict]) -> FastMatch | None:
        if self.bank is None or self.catalog is None:
            return None
        try:
            return match(turn.heard, self.bank, self.catalog, scene, turn_id=turn.turn_id, level=self.level)
        except Exception as e:  # fastpath 의 버그여도 LLM 이 답한다
            _warn(f"turn {turn.turn_id}: fastpath failed: {type(e).__name__}")
            return None

    def _answer(self, turn: _Turn, plan: _Plan) -> _Answer:
        try:
            return self._ask(turn, plan)
        except Exception as e:  # 파서·검증기의 버그여도 2줄은 나간다
            _warn(f"turn {turn.turn_id}: answer failed: {type(e).__name__}")
            return _Answer(fallback_reply("error", turn.turn_id), "fallback", error=type(e).__name__)

    def _ask(self, turn: _Turn, plan: _Plan) -> _Answer:
        if not turn.heard:
            return _Answer(fallback_reply("unclear", turn.turn_id), "template")
        if plan.fast is not None and plan.fast.grade == "exact":
            reply, issues = self._template(turn, plan.fast)
            return _Answer(reply, "template", issues=issues)
        message = build_turn_message(turn.as_request(plan.scene), applied_ops=plan.ops)
        llm_started = time.perf_counter()
        try:
            result = self.llm.complete(message, self.timeout_s)
        except Exception as e:  # LLMTimeout, LLMError, 어댑터의 예상 못 한 오류. 메시지 글은 버린다.
            reason = "timeout" if isinstance(e, LLMTimeout) else "error"
            if isinstance(e, LLMBillingRefused):  # 정해진 한 줄. 로그에는 클래스 이름만 남는다(아래 error)
                _warn(f"turn {turn.turn_id}: LLM refused: API-key billing (subscription only; fix auth, restart)")
            else:
                _warn(f"turn {turn.turn_id}: LLM {type(e).__name__}")
            return _Answer(self._fallback(turn, plan, reason), "fallback", _ms_since(llm_started), type(e).__name__)
        obj = parse_reply_text(result.text)
        reply, issues = validate_reply(
            obj, turn_id=turn.turn_id, catalog=self.catalog, scene=turn.scene,
            applied_ops=plan.applied_ops(), input_has_hangul=turn.korean)
        if obj is None:  # JSON 을 찾지 못했다(issues 에 reply_not_object)
            return _Answer(self._fallback(turn, plan, "error"), "fallback", result.latency_ms, None, issues)
        reply["ops"] = self._second_line_ops(reply["ops"], plan, issues)
        return _Answer(reply, "llm", result.latency_ms, None, issues)

    def _second_line_ops(self, ops: list[dict], plan: _Plan, issues: list[str]) -> list[dict]:
        """LLM 의 hatch 를 빼고, 1줄과 합쳐 한 턴 한도(max_ops)를 넘는 뒤쪽을 자른다. 뒤의 op 만 앞의 op 를 가리키므로 앞부분은 그대로 쓸 수 있다."""
        kept = [op for op in ops if not _is_hatch(op)]
        if len(kept) != len(ops):
            _note(issues, "ops_hatch_dropped")
        room = max(0, self._max_ops - len(plan.ops))
        if len(kept) > room:
            kept = kept[:room]
            _note(issues, "ops_trimmed")
        return kept

    def _fallback(self, turn: _Turn, plan: _Plan, reason: str) -> dict:
        """LLM 이 못 답했을 때. partial 이면 장면은 이미 바뀌었으니 "다시 해 보자" 대신 fastpath 의 바른 문장으로 답한다."""
        if plan.fast is not None:
            return self._template(turn, plan.fast)[0]
        return fallback_reply(reason, turn.turn_id)  # type: ignore[arg-type]

    # ---------------------------------------------------------------- 템플릿 답

    def _template(self, turn: _Turn, fast: FastMatch) -> tuple[dict, list[str]]:
        """fastpath 결과로 만든 완전한 답. ops 는 1줄로 이미 나갔으니 비운다. 검증기를 거쳐 스키마를 지킨다."""
        obj = {
            "say": fast.say,
            "recast": None,
            "choice": None,
            "korean_hint": self._korean_hint(turn, fast),
            "ops": [],
            "mood": None,
            "utterance": {"lang": "en", "words_used": list(fast.word_ids), "errors": []},
        }
        return validate_reply(obj, turn_id=turn.turn_id, catalog=self.catalog)

    def _korean_hint(self, turn: _Turn, fast: FastMatch) -> str | None:
        """"뜻 → English!". 동작·표정 단어면 그 단어의 ko 와 En("{object}" 는 말한 물건, 없으면 기본 물건),
        물건 문법이면 맞은 단어들의 ko 를 이어 쓰고 fastpath 가 만든 영어 구절("큰 빨강 알 → A big red egg!")."""
        words = [self._words[word_id] for word_id in fast.word_ids if word_id in self._words]
        if not words or self.bank is None:
            return None
        head = words[0]
        if head.present.get("type") in ("act", "mood"):
            thing = next((w for w in words[1:] if w.present.get("type") == "sprite"), None)
            return f"{head.ko} → {_exclaim(word_sentence(head, self.bank, thing))}"
        english = self._sentence_in_say(fast.say, turn.turn_id) or _exclaim(words[-1].en)
        return f"{' '.join(word.ko for word in words)} → {english}"

    @staticmethod
    def _sentence_in_say(say: str, turn_id: int) -> str | None:
        """fastpath 대사 "{칭찬} {문장}!" 에서 문장 부분. 대사가 칭찬뿐이면 None."""
        prefix = praise(turn_id) + " "
        return say[len(prefix):] if say.startswith(prefix) and len(say) > len(prefix) else None

    # ---------------------------------------------------------------- 로그

    def _log_turn(self, turn: _Turn, plan: _Plan, answer: _Answer, latency: dict) -> None:
        event: dict = {
            "type": "turn",
            "turn_id": turn.turn_id,
            "heard": turn.heard,  # 이미 redact 를 거쳤다
            "source": answer.source,
            "say": redact(answer.reply["say"]),  # LLM 이 아이 말을 따라 했을 수 있다
            "ops": answer.reply["ops"],
            "latency": latency,
            "hint_requested": turn.hint_requested,  # 스펙 4장: (?) 를 누른 것은 다음 턴에 기록된다
        }
        if plan.ops:
            event["fast_ops"] = plan.ops
        if plan.fast is not None:
            event["fastpath"] = plan.fast.grade
        if answer.error:
            event["error"] = answer.error
        if answer.issues:
            event["issues"] = answer.issues
        self._log(turn.session_id, event)

    def _log(self, session_id: str, event: dict) -> None:
        try:
            self.log.append(session_id, event)
        except Exception as e:  # 로그를 못 써도(어떤 이유든) 놀이는 계속한다
            _warn(f"session log write failed: {type(e).__name__}")
