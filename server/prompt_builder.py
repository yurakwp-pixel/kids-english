"""LLM 에 보내는 글 만들기: 세션 시스템 프롬프트와 턴마다 보내는 한 줄 JSON.

시스템 프롬프트 템플릿(`prompts/chomp_system.md`)에는 JSON 예시가 있어 중괄호가 많다. 그래서 `str.format` 을 쓰지 않고
`{VOCAB}`, `{CATALOG}` 두 자리표시자만 `str.replace` 로 바꾼다.

- `{VOCAB}` ← `vocab_lines(bank, level)`: 지금 레벨까지의 단어, 한 줄에 `English | 한국어 뜻 | 단어 id`.
  id 는 LLM 이 답의 `utterance.words_used` 에 쓰라고 함께 준다(fastpath 템플릿 답도 같은 id 를 쓴다).
- `{CATALOG}` ← `catalog_summary(catalog)`: 캐릭터별 동작과 표정, 물건(색·크기·음식·입기), 색, 크기, 개수, 자리, 한도.
- 단어장이나 카탈로그가 없으면(M1, bench) 둘 다 "(none yet)" 이다.

단어 `en` 의 물건 자리 `{object}`("Throw out the {object}")는 LLM 에게도 아이 힌트에도 그대로 보이면 안 된다.
`word_sentence` 가 들어간 물건 말이나 단어의 기본 물건으로 바꾸고, 둘 다 없으면 그 자리를 뺀다(fastpath 의 대사와 같은 규칙).
"""
from __future__ import annotations

import json
import re
from typing import Sequence

from server.catalog import Catalog
from server.fastpath import DETERMINERS
from server.text import PLACEHOLDER, scrub_surrogates
from server.words import Word, WordBank

NONE_YET = "(none yet)"
# 서버가 스스로 보내는 동작. LLM 에게는 알려 주지 않는다(turn.py 가 LLM 답에서 걸러 낸다).
HATCH_ACTION = "hatch"
_FLAGS = ("color", "size", "food", "wearable")
# `{object}` 와 그 앞의 관사: "Throw out the {object}" -> "Throw out"
_EMPTY_SLOT = re.compile(r"(?:\s+(?:" + "|".join(sorted(DETERMINERS)) + r"))?\s*" + re.escape(PLACEHOLDER),
                         re.IGNORECASE)


def build_system_prompt(template: str, *, vocab: str, catalog: str) -> str:
    return template.replace("{VOCAB}", vocab).replace("{CATALOG}", catalog)


def word_sentence(word: Word, bank: WordBank, object_word: Word | None = None) -> str:
    """단어의 영어 문장(`en`). `{object}` 자리는 object_word(아이가 말한 물건 단어)로, 없으면 단어의 기본 물건
    (`present.needs`, 그다음 `present.removes`)의 이름으로 바꾼다. 기본 물건도 없으면 자리와 그 앞 관사를 뺀다.

    "Chomp the {object}" + apple -> "Chomp the apple", "Throw out the {object}"(기본 물건 없음) -> "Throw out".
    """
    en = word.en
    if PLACEHOLDER not in en:
        return en
    noun = object_word.en.lower() if object_word is not None else _default_noun(word, bank)
    if noun:
        return en.replace(PLACEHOLDER, noun)
    return " ".join(_EMPTY_SLOT.sub("", en).split())


def _default_noun(word: Word, bank: WordBank) -> str | None:
    for key in ("needs", "removes"):
        kind = word.present.get(key)
        if isinstance(kind, str) and kind != PLACEHOLDER:
            sprite = next((w for w in bank.words
                           if w.present.get("type") == "sprite" and w.present.get("kind") == kind), None)
            return sprite.en.lower() if sprite else kind.replace("_", " ")
    return None


def vocab_lines(bank: WordBank, level: int) -> str:
    """`level` 이하의 단어 한 줄씩: `English | 한국어 뜻 | 단어 id` (단어장 파일 순서). 단어가 없으면 "(none yet)"."""
    lines = [f"{word_sentence(word, bank)} | {word.ko} | {word.id}" for word in bank.up_to(level)]
    return "\n".join(lines) or NONE_YET


def catalog_summary(catalog: Catalog) -> str:
    """LLM 이 ops 에 쓸 수 있는 이름과 값. 검증기(`server/reply.py`)가 받아 주는 것과 같은 목록이다."""
    lines = ['Characters ("actor"; also a "target" for a face):']
    for actor, info in catalog.actors.items():
        actions = [action for action in info["actions"] if action != HATCH_ACTION]
        lines.append(f"- {actor}: actions {', '.join(actions)}; faces {', '.join(info['faces'])}")
    lines.append('Things ("kind") and what each can have:')
    for kind, entry in catalog.objects.items():
        flags = [flag for flag in _FLAGS if entry.get(flag)]
        lines.append(f"- {kind}: {', '.join(flags)}" if flags else f"- {kind}")
    lines.append(f"Colors: {', '.join(catalog.colors)}")
    lines.append(f"Sizes: {', '.join(catalog.sizes)}")
    lines.append(f'Count: 1 to {catalog.max_count} in one "add"')
    lines.append(f'Places: {{"zone": one of {", ".join(catalog.zones)}}}'
                 f' or {{"rel": one of {", ".join(catalog.relations)}, "ref": the id of a thing}}')
    lines.append(f"At most {catalog.max_ops} ops in one answer. At most {catalog.max_items} things on screen.")
    return "\n".join(lines)


def build_turn_message(req: dict, applied_ops: Sequence[dict]) -> str:
    """`{"heard": [...n-best 텍스트], "scene": [...], "applied_ops": [...], "hint_requested": bool}` 한 줄 JSON.

    req 는 스펙 5.5 의 턴 요청 모양이다. 턴 처리기는 정리하고 가린(redact) 요청을 넘긴다. 이 함수는 모양만 맞춘다:
    글자가 아닌 후보, 리스트가 아닌 scene 은 빈 것으로 본다. 한글은 읽을 수 있게 그대로 두고(ensure_ascii=False),
    짝 없는 서로게이트만 U+FFFD 로 바꿔 claude.exe 로 보낼 수 있게 한다.
    """
    asr = req.get("asr")
    alternatives = asr.get("alternatives") if isinstance(asr, dict) else None
    heard = [
        alt["t"] for alt in (alternatives if isinstance(alternatives, list) else [])
        if isinstance(alt, dict) and isinstance(alt.get("t"), str)
    ]
    scene = req.get("scene")
    message = {
        "heard": heard,
        "scene": scene if isinstance(scene, list) else [],
        "applied_ops": list(applied_ops),
        "hint_requested": req.get("hint_requested") is True,
    }
    return scrub_surrogates(json.dumps(message, ensure_ascii=False))
