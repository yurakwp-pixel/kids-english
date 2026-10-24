"""장면 물건의 id 는 서버가 정한다: `"{kind}{n}"`(예: "egg3").

fastpath(Task 11)와 검증기(Task 12)가 이 함수 하나를 같이 써서 두 길이 서로 다른 id 를 만들지 않는다.
JS 장면 모델(Task 14)은 서버가 준 id 를 그대로 쓰고, id 가 없을 때만 같은 규칙으로 만든다.

n 은 장면과 같은 답 안의 앞선 op 들(pending)에 이미 있는 그 kind 의 가장 큰 번호 + 1 이다.
빈 번호를 메우지 않으므로("egg1", "egg3" 다음은 "egg4") 지워진 물건의 id 가 다시 쓰이지 않는다.
카탈로그 이름에는 숫자가 없어서(catalog.py `_NAME`) "egg12" 는 언제나 egg 의 12번이다.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Sequence


def next_ids(kind: str, count: int, scene: Sequence[dict], pending: Sequence[dict]) -> list[str]:
    """`kind` 의 새 id `count` 개. scene 항목과 pending op 의 `id`(문자열)와 `ids`(문자열 목록)를 본다.

    LLM 이 낸 깨진 값(문자열이 아닌 id, 목록이 아닌 ids, dict 가 아닌 항목)은 조용히 건너뛴다.
    번호는 ASCII 숫자만 읽는다("egg٣" 같은 다른 숫자는 그 kind 의 id 가 아니다). count 가 1 보다 작으면 [].
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return []
    numbered = re.compile(re.escape(kind) + "([0-9]+)")
    top = 0
    for ident in _ids(scene, pending):
        found = numbered.fullmatch(ident)
        if found:
            top = max(top, int(found.group(1)))
    return [f"{kind}{top + i}" for i in range(1, count + 1)]


def _ids(*groups: Sequence[Any]) -> Iterator[str]:
    for group in groups:
        for entry in group:
            if not isinstance(entry, dict):
                continue
            ident = entry.get("id")
            if isinstance(ident, str):
                yield ident
            many = entry.get("ids")
            if isinstance(many, (list, tuple)):
                yield from (one for one in many if isinstance(one, str))
