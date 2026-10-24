"""놀이판에 나올 수 있는 것의 목록: `shared/catalog.json` 을 읽어 검사하고 Catalog 로 돌려준다.

Python 검증기와 JS 렌더러가 같은 파일을 읽으므로 둘이 서로 다른 목록을 갖는 일이 없다(스펙 5.7).
JSON 은 주석을 쓸 수 없어서 모양을 여기에 적는다.

    {"version": 1,
     "actors":  {"chomp": {"actions": [...], "faces": [...]}, "dino": {"actions": [...], "faces": [...]}},
     "objects": {kind: {"render": "svg" | "emoji", "emoji": "🍎", "color": true, "size": true,
                        "wearable": true, "food": true}},
     "colors": [...], "sizes": [...], "max_count": 5, "zones": [...], "relations": [...],
     "max_ops": 4, "max_items": 20}

- 물건의 `color` `size` `wearable` `food` 는 켜는 것만 적는다(없으면 false).
- `render: "emoji"` 물건은 `emoji` 글자가 꼭 있어야 하고, 색과 크기는 바꿀 수 없다.
- 등장인물 키는 `chomp` 와 `dino` 둘뿐이다. 장면의 대상 id 로도 그대로 쓰인다.

로더는 뒤쪽 코드가 기대는 것만 검사하고, 어긋나면 어느 키가 문제인지 담은 ValueError 를 낸다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

ACTORS = ("chomp", "dino")
RENDERS = ("svg", "emoji")
_FLAGS = ("color", "size", "wearable", "food")
_OBJECT_KEYS = frozenset({"render", "emoji", *_FLAGS})
_TOP_LEVEL = ("version", "actors", "objects", "colors", "sizes", "max_count",
              "zones", "relations", "max_ops", "max_items")
# 이름은 소문자와 밑줄만. 장면 id 가 "egg1" 처럼 물건 이름 + 번호라서, 이름에 숫자가 있으면 id 를 되짚을 수 없다.
_NAME = re.compile(r"[a-z][a-z_]*")  # fullmatch 로 쓴다: `$` 는 끝의 줄바꿈 앞에서도 맞기 때문


@dataclass(frozen=True)
class Catalog:
    """읽기 전용이다: 매핑은 MappingProxyType, 목록은 tuple 이라 가져다 쓰는 쪽이 실수로 고칠 수 없다."""

    version: int
    actors: Mapping[str, Mapping[str, tuple[str, ...]]]  # actor -> {"actions": (...), "faces": (...)}
    objects: Mapping[str, Mapping[str, Any]]  # kind -> JSON 항목 그대로
    colors: tuple[str, ...]
    sizes: tuple[str, ...]
    max_count: int
    zones: tuple[str, ...]
    relations: tuple[str, ...]
    max_ops: int
    max_items: int

    # 아래 조회는 LLM 이 낸 모르는 이름(없는 동작, 이름 자리에 온 숫자나 목록)을 그대로 받는다.
    # 그래서 예외 대신 "없음"(빈 집합 / False)을 돌려주고, 판단은 부르는 쪽(검증기, 단어장 로더)이 한다.

    def actor_actions(self, actor: object) -> frozenset[str]:
        return frozenset(self._actor_list(actor, "actions"))

    def actor_faces(self, actor: object) -> frozenset[str]:
        return frozenset(self._actor_list(actor, "faces"))

    def is_food(self, kind: object) -> bool:
        return self._flag(kind, "food")

    def is_wearable(self, kind: object) -> bool:
        return self._flag(kind, "wearable")

    def can_color(self, kind: object) -> bool:
        return self._flag(kind, "color")

    def can_size(self, kind: object) -> bool:
        return self._flag(kind, "size")

    def _actor_list(self, actor: object, field: str) -> tuple[str, ...]:
        info = self.actors.get(actor) if isinstance(actor, str) else None
        return info[field] if info else ()

    def _flag(self, kind: object, flag: str) -> bool:
        entry = self.objects.get(kind) if isinstance(kind, str) else None
        return bool(entry and entry.get(flag, False))


def load_catalog(path: Path) -> Catalog:
    """`path` 의 카탈로그를 읽어 검사한 뒤 Catalog 로 돌려준다. 문제가 있으면 ValueError(어느 키인지 담긴 메시지)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))  # 깨진 JSON/UTF-8 도 ValueError 의 한 갈래다
    if not isinstance(data, dict):
        raise ValueError("catalog must be a JSON object")
    for key in _TOP_LEVEL:
        if key not in data:
            raise ValueError(f"catalog is missing the key {key!r}")
    return Catalog(
        version=_positive_int(data, "version"),
        actors=_actors(data["actors"]),
        objects=_objects(data["objects"]),
        colors=_names(data["colors"], "colors"),
        sizes=_names(data["sizes"], "sizes"),
        max_count=_positive_int(data, "max_count"),
        zones=_names(data["zones"], "zones"),
        relations=_names(data["relations"], "relations"),
        max_ops=_positive_int(data, "max_ops"),
        max_items=_positive_int(data, "max_items"),
    )


def _positive_int(data: dict, key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer, got {value!r}")
    return value


def _names(value: object, where: str) -> tuple[str, ...]:
    """비어 있지 않고, 이름 규칙에 맞고, 겹치지 않는 문자열 목록."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where} must be a non-empty list, got {value!r}")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _NAME.fullmatch(item):
            raise ValueError(f"{where}: {item!r} is not a lowercase name (a-z and _)")
        if item in seen:
            raise ValueError(f"{where}: duplicate {item!r}")
        seen.add(item)
    return tuple(value)


def _actors(value: object) -> Mapping[str, Mapping[str, tuple[str, ...]]]:
    if not isinstance(value, dict) or set(value) != set(ACTORS):
        raise ValueError(f"actors must have exactly the keys {list(ACTORS)}")
    actors = {}
    for actor in ACTORS:
        info = value[actor]
        if not isinstance(info, dict):
            raise ValueError(f"actors.{actor} must be an object")
        actors[actor] = MappingProxyType({
            field: _names(info.get(field), f"actors.{actor}.{field}") for field in ("actions", "faces")
        })
    return MappingProxyType(actors)


def _objects(value: object) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise ValueError("objects must be a non-empty object")
    objects = {}
    for kind, entry in value.items():
        if not _NAME.fullmatch(kind):
            raise ValueError(f"objects: {kind!r} is not a lowercase name (a-z and _)")
        if kind in ACTORS:
            raise ValueError(f"objects.{kind}: the name belongs to an actor")
        objects[kind] = MappingProxyType(_object_entry(kind, entry))
    return MappingProxyType(objects)


def _object_entry(kind: str, entry: object) -> dict[str, Any]:
    where = f"objects.{kind}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be an object")
    for key in entry:
        if key not in _OBJECT_KEYS:
            raise ValueError(f"{where}: unknown key {key!r}")
    render = entry.get("render")
    if render not in RENDERS:  # `in` 은 tuple 이라 목록/딕셔너리 같은 값에도 안전하다
        raise ValueError(f"{where}: render must be one of {list(RENDERS)}, got {render!r}")
    for flag in _FLAGS:
        if flag in entry and not isinstance(entry[flag], bool):
            raise ValueError(f"{where}.{flag} must be true or false, got {entry[flag]!r}")
    if render == "emoji":
        if not isinstance(entry.get("emoji"), str) or not entry["emoji"].strip():
            raise ValueError(f"{where}: an emoji object needs a non-empty 'emoji'")
        for flag in ("color", "size"):
            if entry.get(flag):
                raise ValueError(f"{where}: an emoji object cannot have {flag}")
    elif "emoji" in entry:
        raise ValueError(f"{where}: only render 'emoji' objects may have 'emoji'")
    return dict(entry)
