"""배울 단어장: `content/words/*.json` 을 읽어 검사하고 WordBank 로 돌려준다.

단어장은 코드가 아니라 데이터다. 단어를 더하는 일은 JSON 한 줄을 더하는 일이다(스펙 5.7).
놀이판에 그릴 수 있는 것(`shared/catalog.json`)과는 따로 두고, 로더가 둘을 맞춰 본다.
JSON 은 주석을 쓸 수 없어서 모양을 여기에 적는다. 파일 하나는 단어 목록(한 줄에 한 단어, UTF-8, 메모장의 BOM 도 된다)이다.

    {"id": "kick-the-ball", "en": "Kick the ball", "ko": "공을 차",
     "accept": ["kick the ball", "kick the {object}", "kick"],
     "level": 1, "category": "action", "source": "shunibini-A",
     "present": {"type": "act", "actor": "dino", "action": "kick", "needs": "ball"}}

- `id`: 소문자·숫자·하이픈. 모든 파일을 통틀어 하나뿐이다.
- `en`: 화면과 힌트에 나오는 영어 문장. `ko`: 힌트의 "뜻 → English" 에 나오는 짧은 우리말(한글이 꼭 있어야 한다).
- `accept`: 이 단어로 쳐 주는 말. 이미 `normalize` 를 거친 모양이어야 하고(소문자, 구두점 없음) 첫 칸은 `normalize(en)` 이다.
  물건 자리는 `{object}` 로 쓴다(act 단어만). 같은 말을 두 단어가 가질 수 없다.
- `level`: 1~5. `category`: 묶음 이름. `source`: `SOURCES` 중 하나(슈니비니 A/B 카드, 나머지는 core).
- `present`: 이 단어가 놀이에 어떻게 나오는지(스펙 5.7).
    {"type": "act", "actor": A, "action": X, "needs": kind?, "removes": kind?}  동작. needs/removes 는 카탈로그 물건이나 "{object}"
    {"type": "sprite", "kind": K}                                              물건이 생긴다
    {"type": "mood", "actor": "all" | A, "face": F}                            표정이 바뀐다("all" 은 두 캐릭터 모두)
    {"type": "attr", "slot": "color" | "size" | "count", "value": V}           꾸미는 말. 혼자서는 아무것도 만들지 않는다

로더는 어긋나면 어느 파일의 어느 단어, 어느 키가 문제인지 담은 ValueError 를 낸다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from server.catalog import Catalog
from server.text import PLACEHOLDER, has_hangul, normalize

SOURCES = ("shunibini-A", "shunibini-B", "core")
SLOTS = ("color", "size", "count")
MAX_LEVEL = 5  # 스펙: 레벨 1~5

_WORD_KEYS = ("id", "en", "ko", "accept", "level", "category", "source", "present")
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_CATEGORY = re.compile(r"[a-z][a-z_]*")  # fullmatch 로 쓴다
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# present.type -> (꼭 있어야 하는 키, 있어도 되는 키)
_PRESENT_KEYS = {
    "act": (("actor", "action"), ("needs", "removes")),
    "sprite": (("kind",), ()),
    "mood": (("actor", "face"), ()),
    "attr": (("slot", "value"), ()),
}


@dataclass(frozen=True)
class Word:
    """단어 하나. `present` 는 파일에서 온 평범한 dict 다: 읽기만 하고 고치지 않는다."""

    id: str
    en: str
    ko: str
    accept: tuple[str, ...]
    level: int
    category: str
    source: str
    present: dict = field(hash=False)  # dict 는 해시가 안 된다; 나머지 칸으로 해시하고 비교는 present 까지 한다


class WordBank:
    """읽기 전용 단어 모음. 조회는 매번 새 list/dict 를 돌려줘서 받은 쪽이 고쳐도 단어장은 그대로다."""

    def __init__(self, words: Iterable[Word]):
        self._words = tuple(words)
        self._attrs: dict[str, dict[str, Any]] = {slot: {} for slot in SLOTS}
        self._objects: dict[str, str] = {}
        for word in self._words:
            kind = word.present.get("type")
            if kind == "attr":
                table = self._attrs[word.present["slot"]]
                for phrase in word.accept:
                    table[normalize(phrase)] = word.present["value"]
            elif kind == "sprite":
                for phrase in word.accept:
                    self._objects[normalize(phrase)] = word.present["kind"]

    @property
    def words(self) -> tuple[Word, ...]:
        return self._words

    def up_to(self, level: int) -> list[Word]:
        """`level` 이하의 단어 전부(파일 순서 그대로)."""
        return [w for w in self._words if w.level <= level]

    def attrs(self, slot: Literal["color", "size", "count"]) -> dict[str, Any]:
        """꾸미는 말: 정규화한 accept -> 값. 개수는 낱말과 숫자가 모두 키다({"three": 3, "3": 3})."""
        if slot not in SLOTS:
            raise ValueError(f"unknown attr slot {slot!r} (known: {list(SLOTS)})")
        return dict(self._attrs[slot])

    def objects(self) -> dict[str, str]:
        """물건 이름: 정규화한 accept(단수와 복수) -> 카탈로그 물건 종류({"balls": "ball"})."""
        return dict(self._objects)


def load_words(dir: Path, catalog: Catalog) -> WordBank:
    """`dir` 의 `*.json` 단어 파일을 이름 순서로 읽어 검사한 뒤 WordBank 로 돌려준다. 문제가 있으면 ValueError."""
    root = Path(dir)
    if not root.is_dir():
        raise ValueError(f"word directory not found: {root}")
    files = sorted(p for p in root.glob("*.json") if p.is_file())
    if not files:
        raise ValueError(f"no *.json word files in {root}")
    words: list[Word] = []
    id_file: dict[str, str] = {}      # id -> 처음 나온 파일
    phrase_owner: dict[str, str] = {}  # accept 말 -> 그 말을 가진 단어 id
    for path in files:
        for index, entry in enumerate(_read_entries(path)):
            word = _parse_word(entry, catalog, path.name, index)
            where = f"{path.name}: word {word.id!r}"
            if word.id in id_file:
                raise ValueError(f"{where}: duplicate id (first defined in {id_file[word.id]})")
            id_file[word.id] = path.name
            for phrase in word.accept:
                if phrase in phrase_owner:
                    raise ValueError(f"{where}: accept {phrase!r} already belongs to word {phrase_owner[phrase]!r}")
                phrase_owner[phrase] = word.id
            words.append(word)
    return WordBank(words)


def _read_entries(path: Path) -> list:
    try:
        # utf-8-sig: 부모가 메모장에서 "UTF-8(BOM)" 으로 저장해도 읽는다(BOM 이 없으면 utf-8 과 같다)
        data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_no_duplicate_keys)
    except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError 는 ValueError 의 한 갈래다
        raise ValueError(f"{path.name}: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: must be a JSON array of words")
    return data


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _parse_word(entry: object, catalog: Catalog, filename: str, index: int) -> Word:
    if not isinstance(entry, dict):
        raise ValueError(f"{filename}: entry #{index} must be an object, got {entry!r}")
    ident = entry.get("id")
    where = f"{filename}: word {ident!r}" if isinstance(ident, str) else f"{filename}: entry #{index}"
    for key in _WORD_KEYS:
        if key not in entry:
            raise ValueError(f"{where}: missing the key {key!r}")
    for key in entry:
        if key not in _WORD_KEYS:
            raise ValueError(f"{where}: unknown key {key!r}")

    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise ValueError(f"{where}: id must be lowercase words joined by hyphens (a-z, 0-9, -), got {ident!r}")
    en = _line(entry["en"], where, "en")
    if has_hangul(en):
        raise ValueError(f"{where}: en must be English, got {en!r}")
    ko = _line(entry["ko"], where, "ko")
    if not has_hangul(ko):
        raise ValueError(f"{where}: ko must be a Korean meaning, got {ko!r}")
    accept = _accept(entry["accept"], en, where)
    level = entry["level"]
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= MAX_LEVEL:
        raise ValueError(f"{where}: level must be an integer from 1 to {MAX_LEVEL}, got {level!r}")
    category = entry["category"]
    if not isinstance(category, str) or not _CATEGORY.fullmatch(category):
        raise ValueError(f"{where}: category must be a lowercase name (a-z and _), got {category!r}")
    source = entry["source"]
    if source not in SOURCES:  # 튜플이라 목록·딕셔너리 같은 값에도 안전하다
        raise ValueError(f"{where}: source must be one of {list(SOURCES)}, got {source!r}")
    present = _present(entry["present"], accept, catalog, where)
    return Word(id=ident, en=en, ko=ko, accept=accept, level=level, category=category, source=source, present=present)


def _line(value: object, where: str, key: str) -> str:
    """화면에 한 줄로 나오는 글자: 비어 있지 않고 줄바꿈 같은 제어 문자가 없다."""
    if not isinstance(value, str) or not value.strip() or _CONTROL.search(value):
        raise ValueError(f"{where}: {key} must be a non-empty single-line string, got {value!r}")
    return value


def _accept(value: object, en: str, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: accept must be a non-empty list, got {value!r}")
    seen: set[str] = set()
    for phrase in value:
        if not isinstance(phrase, str) or not phrase:
            raise ValueError(f"{where}: accept entries must be non-empty strings, got {phrase!r}")
        if has_hangul(phrase):
            raise ValueError(f"{where}: accept {phrase!r} must be English")
        if normalize(phrase) != phrase:
            raise ValueError(f"{where}: accept {phrase!r} is not normalized; write {normalize(phrase)!r}")
        if phrase in seen:
            raise ValueError(f"{where}: accept has duplicate {phrase!r}")
        seen.add(phrase)
    if value[0] != normalize(en):
        raise ValueError(f"{where}: accept[0] must be normalize(en) = {normalize(en)!r}, got {value[0]!r}")
    return tuple(value)


def _present(value: object, accept: tuple[str, ...], catalog: Catalog, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: present must be an object, got {value!r}")
    kind = value.get("type")
    spec = _PRESENT_KEYS.get(kind) if isinstance(kind, str) else None
    if spec is None:
        raise ValueError(f"{where}: present.type must be one of {list(_PRESENT_KEYS)}, got {kind!r}")
    required, optional = spec
    here = f"{where}: present"
    for key in required:
        if key not in value:
            raise ValueError(f"{here} is missing the key {key!r}")
    for key in value:
        if key != "type" and key not in required and key not in optional:
            raise ValueError(f"{here} has an unknown key {key!r} for type {kind!r}")

    has_placeholder = any(PLACEHOLDER in phrase for phrase in accept)
    if has_placeholder and kind != "act":
        raise ValueError(f"{where}: accept may use {PLACEHOLDER} only in an act word")
    if kind == "act":
        _check_act(value, catalog, here, has_placeholder)
    elif kind == "sprite":
        _check_sprite(value, catalog, here)
    elif kind == "mood":
        _check_mood(value, catalog, here)
    else:
        _check_attr(value, catalog, here)
    return dict(value)


def _check_act(value: dict, catalog: Catalog, here: str, has_placeholder: bool) -> None:
    actor, action = value["actor"], value["action"]
    if not catalog.actor_actions(actor):  # 빈 집합 = 모르는 등장인물(문자열이 아닌 값 포함)
        raise ValueError(f"{here}.actor: unknown actor {actor!r} (known: {list(catalog.actors)})")
    if not isinstance(action, str) or action not in catalog.actor_actions(actor):
        raise ValueError(f"{here}.action: {actor} cannot do {action!r} (catalog: {sorted(catalog.actor_actions(actor))})")
    for key in ("needs", "removes"):
        if key not in value:
            continue
        target = value[key]
        if target == PLACEHOLDER:
            if not has_placeholder:
                raise ValueError(f"{here}.{key} is {PLACEHOLDER} but no accept phrase has a {PLACEHOLDER} slot")
        elif not isinstance(target, str) or target not in catalog.objects:
            raise ValueError(f"{here}.{key}: unknown object {target!r} (use a catalog kind or {PLACEHOLDER})")
    # 멍멍이는 음식만 문다(CLAUDE.md): 정해진 대상이 음식이 아니면 단어장에서부터 막는다
    if actor == "chomp" and action == "chomp" and "needs" in value and value["needs"] != PLACEHOLDER:
        if not catalog.is_food(value["needs"]):
            raise ValueError(f"{here}.needs: chomp only bites food, {value['needs']!r} is not food")


def _check_sprite(value: dict, catalog: Catalog, here: str) -> None:
    kind = value["kind"]
    if not isinstance(kind, str) or kind not in catalog.objects:
        raise ValueError(f"{here}.kind: unknown object {kind!r}")


def _check_mood(value: dict, catalog: Catalog, here: str) -> None:
    actor, face = value["actor"], value["face"]
    if actor == "all":
        # 두 캐릭터 모두의 표정이 바뀌므로 둘 다 지을 수 있는 표정만 쓴다
        faces = frozenset.intersection(*(catalog.actor_faces(a) for a in catalog.actors))
    else:
        faces = catalog.actor_faces(actor)
        if not faces:
            raise ValueError(f"{here}.actor: unknown actor {actor!r} (known: 'all' and {list(catalog.actors)})")
    if not isinstance(face, str) or face not in faces:
        raise ValueError(f"{here}.face: {actor} cannot make the face {face!r} (catalog: {sorted(faces)})")


def _check_attr(value: dict, catalog: Catalog, here: str) -> None:
    slot, attr = value["slot"], value["value"]
    if slot == "color":
        ok = isinstance(attr, str) and attr in catalog.colors
    elif slot == "size":
        ok = isinstance(attr, str) and attr in catalog.sizes
    elif slot == "count":
        ok = isinstance(attr, int) and not isinstance(attr, bool) and 1 <= attr <= catalog.max_count
    else:
        raise ValueError(f"{here}.slot: must be one of {list(SLOTS)}, got {slot!r}")
    if not ok:
        raise ValueError(f"{here}.value: {attr!r} is not a valid {slot} (catalog: colors {list(catalog.colors)}, "
                         f"sizes {list(catalog.sizes)}, counts 1..{catalog.max_count})")
