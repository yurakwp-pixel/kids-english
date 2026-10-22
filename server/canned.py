"""준비된 대사. LLM 이 늦거나, 실패하거나, 아이 말을 못 알아들었을 때 대신 답한다.

아이 UX 규칙: 짧고 따뜻하게, 15단어 이하, `say` 에 한글 없음, "Wrong"/"No" 같은 부정 표현 없음.
한글은 `korean_hint` 에만, "뜻 → English sentence" 형태로 쓴다.
돌려주는 dict 는 스펙 5.5 의 LLM 출력 스키마를 그대로 만족한다(validator 를 거쳐도 그대로 통과).
"""
from __future__ import annotations

from typing import Literal

Reason = Literal["timeout", "error", "unclear"]

_PRAISE = ["Good job!", "Excellent!", "Thumbs up!", "Wow!", "Well done!"]

# (say, korean_hint) 묶음. turn_id 로 돌려 가며 써서 같은 말이 연달아 나오지 않게 한다.
_UNCLEAR = [
    ("Say it again! I'm listening.", "다시 말해 볼까? → Say it again!"),
    ("Oh! Say it one more time!", "한 번 더 말해 볼까? → Say it one more time!"),
]
_TRY_AGAIN = [
    ("Hmm! Let's try that again!", "우리 다시 해 보자! → Let's try that again!"),
    ("Hmm! One more time, please!", "한 번 더 해 볼까? → One more time, please!"),
]
_LINES: dict[str, list[tuple[str, str]]] = {
    "unclear": _UNCLEAR,
    "timeout": _TRY_AGAIN,
    "error": _TRY_AGAIN,
}


def praise(turn_id: int) -> str:
    """칭찬 한마디. turn_id 에 따라 다섯 가지를 돌아가며 쓴다."""
    return _PRAISE[turn_id % len(_PRAISE)]


def fallback_reply(reason: Reason, turn_id: int) -> dict:
    """LLM 없이 내보내는, 스키마를 만족하는 완전한 답. 매번 새 dict 를 돌려준다.

    알 수 없는 reason 이 와도 놀이가 멈추지 않도록 "다시 해 보자" 쪽으로 처리한다.
    """
    lines = _LINES.get(reason, _TRY_AGAIN)
    say, korean_hint = lines[turn_id % len(lines)]
    return {
        "say": say,
        "recast": None,
        "choice": None,
        "korean_hint": korean_hint,
        "ops": [],
        "mood": None,
        "utterance": {"lang": "unknown", "words_used": [], "errors": []},
    }
