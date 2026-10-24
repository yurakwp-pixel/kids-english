"""아이 말(음성인식 결과)과 화면 글자를 다루는 작은 도구. 다른 모듈에 기대지 않는다.

- `normalize`: 비교용으로 소문자·구두점 없음·공백 하나로 맞춘다(fastpath 가 단어장 accept 와 견줄 때도 쓴다).
  단어장 accept 의 물건 자리 표시 `{object}` 만은 그대로 남긴다(`PLACEHOLDER`).
- `has_hangul`: 한글이 한 글자라도 있는지. 서버 전체가 이 함수 하나로 판단한다(검증기, 턴 처리기).
- `is_noise`: 빈 말이나 "uh", "um" 같은 군소리만 있는 말이면 True(그때는 LLM 을 부르지 않는다).
- `scrub_surrogates`, `clean_text`: 밖에서 온 글자를 안전하게 만든다. JSON 의 `"\\ud800"` 같은 짝 없는 서로게이트는
  UTF-8 로 인코딩할 수 없어서, claude.exe 로 보내거나 파일에 쓰는 순간 오류가 난다.
"""
from __future__ import annotations

import re
import unicodedata

# 한글 음절, 자모(호환 자모 포함), 확장 자모, 반각 한글. 자모만 있는 "ㅋㅋ" 도 한글로 본다.
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿ﾠ-ￜ]")
_SURROGATE = re.compile("[\ud800-\udfff]")
_APOSTROPHES = frozenset("'‘’ʼ`")

# 단어장 accept 의 물건 자리 표시("throw out the {object}"). normalize 가 구두점처럼 지우지 않는 유일한 중괄호 말이다.
PLACEHOLDER = "{object}"

# 말 사이에 끼는 소리. 이것만 들렸으면 아이가 아직 말하지 않은 것으로 본다.
FILLERS = frozenset({"uh", "um", "hmm", "ah", "er"})


def has_hangul(text: object) -> bool:
    return isinstance(text, str) and _HANGUL.search(text) is not None


def normalize(text: object) -> str:
    """"  Kick, the BALL!! " -> "kick the ball". 글자가 아니면 빈 문자열.

    전각 글자는 보통 글자로 바꾸고(NFKC), 아포스트로피는 지우고("let's" -> "lets"),
    그 밖의 구두점·기호·제어 문자는 띄어쓰기로 바꾼 뒤 공백을 하나로 줄인다.
    자리 표시 `{object}` 는 지우지 않고 앞뒤를 띄운 한 덩어리로 남긴다:
    "Throw out the {object}!" -> "throw out the {object}". 다른 중괄호 말("{foo}")은 여느 구두점과 같다.
    """
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text).lower()  # 전각 "｛Object｝" 도 여기서 "{object}" 가 된다
    pieces = [_strip_punctuation(piece) for piece in folded.split(PLACEHOLDER)]
    return " ".join(f" {PLACEHOLDER} ".join(pieces).split())


def _strip_punctuation(text: str) -> str:
    out = []
    for ch in text:
        if ch in _APOSTROPHES:
            continue
        out.append(" " if unicodedata.category(ch)[0] in "PSCZ" else ch)
    return "".join(out)


def is_noise(text: object) -> bool:
    """빈 말, 공백뿐인 말, 군소리(FILLERS)만 있는 말."""
    return all(word in FILLERS for word in normalize(text).split())


def scrub_surrogates(text: str) -> str:
    """짝 없는 서로게이트를 U+FFFD(�)로 바꾼다. 결과는 언제나 UTF-8 로 인코딩된다."""
    return _SURROGATE.sub("�", text)


def clean_text(text: object, max_chars: int = 200) -> str:
    """밖에서 온 한 줄 글자: 서로게이트를 고치고 공백을 하나로 줄인 뒤 max_chars 로 자른다. 글자가 아니면 ""."""
    if not isinstance(text, str):
        return ""
    return " ".join(scrub_surrogates(text).split())[:max_chars]
