"""LLM 의 답을 읽고(parse_reply_text) 검증하는(validate_reply) 안전망.

LLM 이 무엇을 내보내든(코드펜스, 설명문, 잘린 JSON, 엉뚱한 타입, 한글 `say`, 모르는 키) 서버 밖으로 나가는 답은
언제나 `shared/schemas/turn_reply.schema.json` 을 만족한다. 필드마다 따로 고치고, 고친 내역은 issue 문자열로 남긴다.
고칠 수 없는 필드는 "없음"에 해당하는 기본값(null, [], 기본 utterance)으로 바꾸고, `say` 가 통째로 못 쓸 때는
`server.canned` 의 대사로 바꾼다. 놀이는 멈추지 않는다.

M1 범위: LLM 이 낸 `ops` 는 모두 버린다. 카탈로그 기반 검사는 Task 12 가 `_validate_ops` 안에 붙인다.

issue 문자열 (고친 것이 있을 때만 들어간다):
  reply_not_object                  답이 JSON 객체가 아님 -> 통째로 canned "error" 답
  say_missing / say_not_string / say_empty
  say_hangul / say_markup / say_url  문제가 있는 문장만 빼고 나머지를 쓴다
  say_too_long                      15단어(200자)를 넘는 뒤쪽 문장을 뺀다
  say_replaced                      쓸 수 있는 문장이 없어 canned 대사로 바꿈(korean_hint 도 짝에 맞춰 바꾸고 choice 는 버린다)
  recast_invalid / recast_ask_repeat_defaulted
  choice_invalid / choice_item_dropped / choice_trimmed / choice_too_few
  korean_hint_invalid / korean_hint_unsafe
  ops_invalid / ops_dropped_hangul / ops_dropped_no_catalog / ops_catalog_check_pending
  mood_invalid
  utterance_missing / utterance_invalid / utterance_lang_invalid / words_used_fixed / errors_fixed
"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

from server.canned import fallback_reply

MAX_SAY_WORDS = 15
MAX_ENGLISH_CHARS = 200  # say / recast.model / choice 한 개. 15단어여도 글자 수가 말이 안 되면 거른다.
MAX_HINT_CHARS = 150
MIN_CHOICES, MAX_CHOICES = 2, 3
MAX_OPS = 4  # 스키마의 maxItems 와 같은 값. Task 12 의 `_validate_ops` 가 앞에서 이만큼만 남길 때 쓴다.
MAX_WORDS_USED, MAX_WORD_ID_CHARS = 20, 60
MAX_ERRORS, MAX_ERROR_VALUE_CHARS, MAX_ERROR_KEY_CHARS = 10, 100, 30

ACTORS = ("chomp", "dino")
LANGS = ("en", "ko", "mixed", "unknown")

# 한글 음절, 자모(호환 자모 포함), 확장 자모, 반각 한글. 자모만 있는 "ㅋㅋ" 도 한글로 본다.
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿ﾠ-ￜ]")
_URL = re.compile(
    r"[a-z][a-z0-9+.-]*://|www\.|\b[a-z0-9-]+\.(?:com|net|org|kr|io|app|dev|tv|xyz)\b",
    re.IGNORECASE,
)
_ALNUM = re.compile(r"[A-Za-z0-9]")
_FACE = re.compile(r"[a-z][a-z-]{0,19}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_OBJECT_START = re.compile(r"\{\s*[\"}]")  # JSON 객체처럼 보이는 여는 중괄호: 설명문 속 `{is}` 같은 것은 건너뛴다
_DECODER = json.JSONDecoder()


def parse_reply_text(text: str) -> dict | None:
    """텍스트 안에서 JSON 객체 하나를 찾아 dict 로 돌려준다. 없으면 None.

    코드펜스와 앞뒤 설명문은 괜찮다. 규칙: JSON 객체처럼 보이는 첫 `{` 가 답을 정한다. 그 객체가 잘렸거나
    문법이 틀렸으면 None 이다(안쪽 조각을 답으로 착각하지 않는다). 너무 깊은 중첩도 None.
    """
    if not isinstance(text, str):
        return None
    match = _OBJECT_START.search(text)
    if match is None:
        return None
    try:
        value, _end = _DECODER.raw_decode(text, match.start())
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def validate_reply(
    obj: Any,
    *,
    turn_id: int,
    catalog: Any = None,
    scene: Sequence[dict] = (),
    applied_ops: Sequence[dict] = (),
    input_has_hangul: bool = False,
) -> tuple[dict, list[str]]:
    """어떤 입력이 와도 스키마를 만족하는 답과, 고친 내역(issues)을 돌려준다. 예외를 내지 않는다.

    `catalog` 의 타입은 Task 9 의 `Catalog` 다(아직 없어서 Any). `catalog is None` 이면 `ops` 는 모두 버린다(M1).
    """
    turn_id = turn_id if isinstance(turn_id, int) and not isinstance(turn_id, bool) else 0
    if not isinstance(obj, dict):
        return fallback_reply("error", turn_id), ["reply_not_object"]

    issues: list[str] = []
    say = _clean_say(obj, issues)
    if say is None:
        # 말이 바뀌었으니, 그 말에 딸린 보기(choice)는 질문 없이 남지 않게 버리고 힌트는 canned 짝으로 바꾼다.
        canned = fallback_reply("error", turn_id)
        say, korean_hint, choice = canned["say"], canned["korean_hint"], None
        _note(issues, "say_replaced")
    else:
        choice = _clean_choice(obj.get("choice"), issues)
        korean_hint = _clean_korean_hint(obj.get("korean_hint"), issues)
    recast = _clean_recast(obj.get("recast"), issues)
    ops, ops_issues = _validate_ops(
        obj.get("ops"),
        catalog=catalog,
        scene=scene,
        applied_ops=applied_ops,
        input_has_hangul=input_has_hangul,
    )
    issues.extend(ops_issues)
    mood = _clean_mood(obj.get("mood"), issues)
    utterance = _clean_utterance(obj.get("utterance"), issues)

    reply = {  # 키 순서는 스펙 5.5 그대로, `say` 가 첫 키. 모르는 키는 여기서 버려진다.
        "say": say,
        "recast": recast,
        "choice": choice,
        "korean_hint": korean_hint,
        "ops": ops,
        "mood": mood,
        "utterance": utterance,
    }
    return reply, issues


# ---------------------------------------------------------------- ops (Task 12 가 여기를 채운다)


def _validate_ops(
    raw: Any,
    *,
    catalog: Any,
    scene: Sequence[dict],
    applied_ops: Sequence[dict],
    input_has_hangul: bool,
) -> tuple[list[dict], list[str]]:
    """LLM 의 ops 를 검사해 (살아남은 ops, issues) 를 돌려준다.

    M1: 아무것도 통과시키지 않는다. Task 12 는 이 함수 안에서 `catalog`, `scene`, `applied_ops` 로
    op 종류·속성·target·중복·개수(최대 MAX_OPS)를 검사하고, 통과한 op 만 돌려주면 된다.
    """
    if raw is None or raw == []:
        return [], []
    if not isinstance(raw, list):
        return [], ["ops_invalid"]
    if input_has_hangul:  # 한글이 들어간 입력의 답에는 ops 가 없어야 한다(전역 제약)
        return [], ["ops_dropped_hangul"]
    if catalog is None:
        return [], ["ops_dropped_no_catalog"]
    return [], ["ops_catalog_check_pending"]  # 카탈로그가 와도 Task 12 전에는 검증 안 된 op 를 내보내지 않는다


# ---------------------------------------------------------------- 필드별 고치기


def _note(issues: list[str], name: str) -> None:
    if name not in issues:
        issues.append(name)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _text_problem(text: str) -> str | None:
    """아이에게 보여 주고 읽어 줄 영어에 들어가면 안 되는 것: 한글, `<`, URL."""
    if _HANGUL.search(text):
        return "hangul"
    if "<" in text:
        return "markup"
    if _URL.search(text):
        return "url"
    return None


def _clean_say(obj: dict, issues: list[str]) -> str | None:
    """쓸 수 있는 `say` 를 돌려준다. 문제 있는 문장은 빼고, 15단어를 넘는 뒤쪽 문장도 뺀다. 남는 게 없으면 None."""
    if "say" not in obj:
        _note(issues, "say_missing")
        return None
    raw = obj["say"]
    if not isinstance(raw, str):
        _note(issues, "say_not_string")
        return None
    text = _squash(raw)
    if not text:
        _note(issues, "say_empty")
        return None

    sentences = []
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) > MAX_ENGLISH_CHARS:
            # 한 문장이 이미 한도를 넘는다. 이 문장부터 뒤는 어차피 못 쓰니 정규식을 돌리지 않고 끊는다
            # (아주 긴 글에 URL 정규식을 돌리면 느려진다).
            _note(issues, "say_too_long")
            break
        problem = _text_problem(sentence)
        if problem:
            _note(issues, f"say_{problem}")
        else:
            sentences.append(sentence)

    kept: list[str] = []
    words = chars = 0
    for sentence in sentences:
        words += len(sentence.split())
        chars += len(sentence) + (1 if kept else 0)
        if words > MAX_SAY_WORDS or chars > MAX_ENGLISH_CHARS:
            _note(issues, "say_too_long")
            break
        kept.append(sentence)

    result = " ".join(kept)
    if kept and not _ALNUM.search(result):  # "..." 같은 소리 없는 말
        _note(issues, "say_empty")
        return None
    return result or None


def _clean_english(raw: Any) -> str | None:
    """recast.model, choice 한 개처럼 통째로 쓰거나 버리는 영어 한마디."""
    if not isinstance(raw, str):
        return None
    text = _squash(raw)
    if (
        not text
        or len(text) > MAX_ENGLISH_CHARS  # 길이를 먼저 본다: 정규식은 짧은 글에만 돌린다
        or len(text.split()) > MAX_SAY_WORDS
        or _text_problem(text)
        or not _ALNUM.search(text)
    ):
        return None
    return text


def _clean_recast(raw: Any, issues: list[str]) -> dict | None:
    if raw is None:
        return None
    model = _clean_english(raw.get("model")) if isinstance(raw, dict) else None
    if model is None:
        _note(issues, "recast_invalid")
        return None
    ask_repeat = raw.get("ask_repeat")
    if not isinstance(ask_repeat, bool):
        ask_repeat = True  # 따라 말하게 하려고 만든 recast 다
        _note(issues, "recast_ask_repeat_defaulted")
    return {"model": model, "ask_repeat": ask_repeat}


def _clean_choice(raw: Any, issues: list[str]) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        _note(issues, "choice_invalid")
        return None
    options: list[str] = []
    for item in raw:
        text = _clean_english(item)
        if text is None or text.lower() in (o.lower() for o in options):
            _note(issues, "choice_item_dropped")
        else:
            options.append(text)
    if len(options) > MAX_CHOICES:
        options = options[:MAX_CHOICES]
        _note(issues, "choice_trimmed")
    if len(options) < MIN_CHOICES:
        if raw:
            _note(issues, "choice_too_few")
        return None
    return options


def _clean_korean_hint(raw: Any, issues: list[str]) -> str | None:
    """한글이 들어갈 수 있는 유일한 필드. 모양("뜻 → English")은 강제하지 않고 안전만 본다."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        _note(issues, "korean_hint_invalid")
        return None
    text = _squash(raw)
    if not text:
        return None
    if len(text) > MAX_HINT_CHARS or "<" in text or _URL.search(text):
        _note(issues, "korean_hint_unsafe")
        return None
    return text


def _clean_mood(raw: Any, issues: list[str]) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict) and isinstance(raw.get("actor"), str) and isinstance(raw.get("face"), str):
        actor, face = raw["actor"].strip().lower(), raw["face"].strip().lower()
        if actor in ACTORS and _FACE.fullmatch(face):
            return {"actor": actor, "face": face}
    _note(issues, "mood_invalid")
    return None


def _clean_utterance(raw: Any, issues: list[str]) -> dict:
    result: dict = {"lang": "unknown", "words_used": [], "errors": []}
    if raw is None:
        _note(issues, "utterance_missing")
        return result
    if not isinstance(raw, dict):
        _note(issues, "utterance_invalid")
        return result

    lang = raw.get("lang")
    lang = lang.strip().lower() if isinstance(lang, str) else None
    if lang in LANGS:
        result["lang"] = lang
    else:
        _note(issues, "utterance_lang_invalid")

    words_used = raw.get("words_used", [])
    result["words_used"] = _clean_words_used(words_used)
    if result["words_used"] != words_used:
        _note(issues, "words_used_fixed")

    errors = raw.get("errors", [])
    result["errors"] = _clean_errors(errors)
    if result["errors"] != errors:
        _note(issues, "errors_fixed")
    return result


def _clean_words_used(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    ids = [item.strip() for item in raw if isinstance(item, str)]
    return [i for i in ids if i and len(i) <= MAX_WORD_ID_CHARS][:MAX_WORDS_USED]


def _clean_errors(raw: Any) -> list[dict]:
    """errors 는 `{"said": ..., "better": ...}` 같은 작은 메모다. 문자열 값만 남기고 크기를 제한한다."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        note = {
            key: value
            for key, value in item.items()
            if isinstance(key, str)
            and len(key) <= MAX_ERROR_KEY_CHARS
            and isinstance(value, str)
            and value.strip()
            and len(value) <= MAX_ERROR_VALUE_CHARS
        }
        if note:
            cleaned.append(note)
    return cleaned[:MAX_ERRORS]
