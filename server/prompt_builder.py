"""LLM 에 보내는 글 만들기: 세션 시스템 프롬프트와 턴마다 보내는 한 줄 JSON.

시스템 프롬프트 템플릿(`prompts/chomp_system.md`)에는 JSON 예시가 있어 중괄호가 많다. 그래서 `str.format` 을 쓰지 않고
`{VOCAB}`, `{CATALOG}` 두 자리표시자만 `str.replace` 로 바꾼다. M1 에는 단어장과 카탈로그가 없어 둘 다 "(none yet)" 이다
(Task 13 이 실제 단어장 줄과 카탈로그 요약을 넣는다).
"""
from __future__ import annotations

import json
from typing import Sequence

from server.text import scrub_surrogates

NONE_YET = "(none yet)"


def build_system_prompt(template: str, *, vocab: str, catalog: str) -> str:
    return template.replace("{VOCAB}", vocab).replace("{CATALOG}", catalog)


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
