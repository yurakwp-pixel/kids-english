"""fastpath: 인식된 말을 LLM 없이 장면 조작(ops)과 템플릿 대사로 바꾼다(스펙 5.6, ADR-0004).

"Kick the ball!" 이 들리면 1ms 안에 공이 생기고 공룡이 찬다. 맞는 틀이 없으면 None 을 돌려주고 LLM 이 답한다.

문법은 둘뿐이다. 말은 `normalize` 를 거친 낱말 목록으로 본다.

1. 단어 문장: act/mood 단어의 `accept` 와 똑같으면 맞는다. accept 의 `{object}` 자리에는 물건 문법 하나가 들어간다
   ("kick the {object}" -> "kick the big red egg"). 앞의 호칭("chomp", "dino", "hey chomp", "hey dino")은 떼어 내고
   그 캐릭터가 할 수 있는 동작/표정이면 행위자로 쓴다. 호칭 뒤에는 단어 문장만 온다("chomp apple" 은 사과를 만들지 않는다).
2. 물건 문법: `[관사|개수]? [크기]? [색]? 물건` ("big red egg", "two balls", "a big red egg").
   개수는 이 모듈의 고정 표(숫자, one~twenty)와 단어장의 개수 단어로 읽고 `catalog.max_count` 로 자른다.
   같은 꾸밈말의 되풀이("big big egg")와 순서가 바뀐 말("red big egg")도 받지만 partial 이다. 서로 다른 값이 부딪치면 맞지 않는다.

판정: 맞은 패턴이 accept[0] 이거나 `{object}` 바로 앞이 관사(the/your/a/an/my)인 틀이면 exact, 다른 변형은 partial.
물건 문법만으로 맞으면 exact. 자리에 들어간 물건 말이 정순서가 아니면 partial 이다.

아이 말 앞뒤의 군소리("um kick the ball", "kick the ball uh")는 뗀다: 후보를 통째로 맞춰 보고 맞는 읽기가 없을 때만
앞뒤의 `FILLERS`("uh", "um", "hmm", "ah", "er")를 떼고 한 번 더 본다. 뗀 군소리는 판정을 바꾸지 않는다("um kick ball" 도 partial).
통째로 먼저 보는 까닭: 단어장에 "uh oh" 같은 말이 들어와도 그대로 맞게 하려고(G4).

지키는 것: 멍멍이는 음식만 문다(아니면 그 읽기는 맞지 않음), `put_on` 은 입을 수 있는 물건만, 색·크기를 못 가지는 물건은
그 속성을 버린다, `add` 에는 서버가 정한 id(`server/ids.py`)를 넣고 `place` 는 넣지 않는다, ops 는 `catalog.max_ops` 까지.
한글이 든 후보와 글자 그대로의 중괄호(`{`, `}`)가 든 후보는 건너뛴다. 입력(장면, 단어장)은 고치지 않는다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from server.canned import praise
from server.catalog import ACTORS, Catalog
from server.ids import next_ids
from server.text import FILLERS, PLACEHOLDER, has_hangul, normalize
from server.words import Word, WordBank

Grade = Literal["exact", "partial"]

DETERMINERS = frozenset({"the", "your", "a", "an", "my"})
ARTICLES = frozenset({"a", "an"})  # 물건 문법 맨 앞에서만 "하나" 로 읽는다
# R8: 개수를 읽고 자르는 고정 표. 단어장의 one~five 는 배우는 단어로 그대로 남는다(그 단어 id 가 word_ids 에 들어간다).
NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
NUMBERS = {word: n for n, word in enumerate(NUMBER_WORDS, start=1)}
# 긴 것부터 본다: "hey chomp jump" 은 "hey chomp" 를 뗀다
VOCATIVES = ((("hey", "chomp"), "chomp"), (("hey", "dino"), "dino"), (("chomp",), "chomp"), (("dino",), "dino"))
MAX_SAY_WORDS = 15

_SLOT_RANK = {"count": 0, "size": 1, "color": 2}  # 영어 꾸밈말 순서: 개수, 크기, 색
_DIGITS = re.compile("[0-9]+")
_EMPTY_SLOT = re.compile(r"(?:\s+(?:the|your|a|an|my))?\s*" + re.escape(PLACEHOLDER), re.IGNORECASE)


@dataclass(frozen=True)
class FastMatch:
    ops: list[dict]
    say: str
    grade: Grade
    word_ids: list[str]


@dataclass(frozen=True)
class _Thing:
    """물건 문법 하나를 읽은 것."""

    kind: str
    word: Word
    said: str  # 아이가 말한 물건 이름(정규화한 낱말)
    count: int | None  # 말한 개수(잘라 낸 뒤). None = 말하지 않음
    size: str | None  # 카탈로그가 허락한 것만 남는다
    color: str | None
    canonical: bool  # 되풀이가 없고 [개수] [크기] [색] 순서다
    word_ids: tuple[str, ...]

    def attrs(self) -> dict[str, str]:
        return {key: value for key, value in (("size", self.size), ("color", self.color)) if value}


@dataclass(frozen=True)
class _Template:
    word: Word
    index: int  # accept 안의 자리
    head: tuple[str, ...]  # `{object}` 앞 낱말
    tail: tuple[str, ...]  # `{object}` 뒤 낱말
    determiner: bool  # `{object}` 바로 앞이 관사


@dataclass(frozen=True)
class _Found:
    grade: Grade
    ops: list[dict]
    sentence: str  # 칭찬 뒤에 붙는 바른 영어 문장
    word_ids: tuple[str, ...]


def match(alternatives: Sequence[str], bank: WordBank, catalog: Catalog, scene: Sequence[dict], *,
          turn_id: int, level: int = 2) -> FastMatch | None:
    """인식 후보를 순서대로 보고 처음 맞는 것을 FastMatch 로. 맞는 것이 없으면 None(그때는 LLM 이 답한다).

    `level` 이하의 단어만 쓴다(문장, 꾸밈말, 물건 모두). 한 후보 안에서는 exact 읽기가 partial 읽기보다 앞선다.
    """
    if isinstance(alternatives, str):  # 후보 하나를 그냥 넘겨도 글자 하나하나로 쪼개지 않는다
        alternatives = [alternatives]
    grammar = _Grammar(bank, level)
    items = list(scene)
    for alternative in alternatives:
        tokens = _tokens(alternative)
        if not tokens:
            continue
        found = _read(tokens, grammar, catalog, items)
        core = _without_fillers(tokens)
        if found is None and core and core != tokens:  # "um kick the ball", "kick the ball uh"
            found = _read(core, grammar, catalog, items)
        if found is not None:
            return FastMatch(ops=found.ops[:catalog.max_ops], say=_say(turn_id, found.sentence),
                             grade=found.grade, word_ids=list(found.word_ids))
    return None


def _tokens(alternative: object) -> list[str]:
    """비교할 낱말 목록. 글자가 아니거나, 한글이 있거나, 중괄호가 있으면 [] (그 후보는 건너뛴다)."""
    if not isinstance(alternative, str) or has_hangul(alternative):
        return []
    folded = unicodedata.normalize("NFKC", alternative)  # 전각 "｛" 도 "{" 가 된다
    if "{" in folded or "}" in folded:  # 아이 말에 `{object}` 가 들어와 틀 자체와 맞는 일을 막는다
        return []
    return normalize(alternative).split()


def _without_fillers(tokens: list[str]) -> list[str]:
    """앞뒤의 군소리(`server/text.FILLERS`)를 뗀 낱말. 가운데의 군소리("kick uh the ball")는 그대로 둔다."""
    start, end = 0, len(tokens)
    while start < end and tokens[start] in FILLERS:
        start += 1
    while end > start and tokens[end - 1] in FILLERS:
        end -= 1
    return tokens[start:end]


class _Grammar:
    """한 번의 match 에 쓰는 표: `level` 이하 단어의 문장, 틀, 물건 이름, 꾸밈말."""

    def __init__(self, bank: WordBank, level: int):
        self.literals: dict[str, tuple[Word, int]] = {}
        self.templates: list[_Template] = []
        self.nouns: dict[str, Word] = {}
        self.modifiers: dict[str, tuple[str, Any, str | None]] = {}  # 말 -> (slot, 값, 단어 id)
        words = bank.up_to(level)
        for word in words:
            kind = word.present["type"]
            if kind in ("act", "mood"):
                for index, phrase in enumerate(word.accept):
                    tokens = phrase.split()
                    if PLACEHOLDER not in tokens:
                        self.literals.setdefault(phrase, (word, index))
                    elif kind == "act" and tokens.count(PLACEHOLDER) == 1:  # 자리가 둘인 틀은 쓰지 않는다
                        at = tokens.index(PLACEHOLDER)
                        self.templates.append(_Template(word, index, tuple(tokens[:at]), tuple(tokens[at + 1:]),
                                                        at > 0 and tokens[at - 1] in DETERMINERS))
            elif kind == "sprite":
                for phrase in word.accept:
                    self.nouns.setdefault(phrase, word)
            elif kind == "attr":
                for phrase in word.accept:
                    self.modifiers.setdefault(phrase, (word.present["slot"], word.present["value"], word.id))
        for phrase, n in NUMBERS.items():
            self.modifiers.setdefault(phrase, ("count", n, None))
        self.longest_modifier = max(len(phrase.split()) for phrase in self.modifiers)
        # 대사에서 물건 이름을 부를 때 쓰는 단어(레벨 안의 것이 먼저)
        self.kind_words: dict[str, Word] = {}
        for word in [*words, *bank.words]:
            if word.present["type"] == "sprite":
                self.kind_words.setdefault(word.present["kind"], word)

    def thing(self, tokens: list[str], catalog: Catalog, *, article_ok: bool) -> _Thing | None:
        """`[관사|개수]? [크기]? [색]? 물건`. 물건 이름은 뒤쪽 낱말이고, 긴 이름부터 맞춰 본다("teddy bear")."""
        for start in range(len(tokens)):
            said = " ".join(tokens[start:])
            word = self.nouns.get(said)
            if word is None:
                continue
            read = self._modifiers(tokens[:start], article_ok)
            if read is None:
                continue
            values, canonical, ids = read
            count = values.get("count")
            if count is not None:
                if count < 1:  # "0 eggs": 아무것도 만들지 않는다
                    continue
                count = min(count, catalog.max_count)
            kind = word.present["kind"]
            return _Thing(kind=kind, word=word, said=said, count=count,
                          size=values.get("size") if catalog.can_size(kind) else None,
                          color=values.get("color") if catalog.can_color(kind) else None,
                          canonical=canonical, word_ids=tuple(dict.fromkeys([*ids, word.id])))
        return None

    def _modifiers(self, tokens: list[str], article_ok: bool) -> tuple[dict[str, Any], bool, list[str]] | None:
        values: dict[str, Any] = {}
        ids: list[str] = []
        canonical, last_rank, at = True, -1, 0
        if tokens and tokens[0] in ARTICLES:
            if not article_ok:  # "kick the a ball"
                return None
            values["count"], last_rank, at = 1, 0, 1
        while at < len(tokens):
            hit = self._modifier_at(tokens, at)
            if hit is None:
                return None
            (slot, value, word_id), width = hit
            if slot in values:
                if values[slot] != value:  # "big small egg", "a two eggs"
                    return None
                canonical = False
            values[slot] = value
            rank = _SLOT_RANK[slot]
            if rank < last_rank:
                canonical = False
            last_rank = max(last_rank, rank)
            if word_id:
                ids.append(word_id)
            at += width
        return values, canonical, ids

    def _modifier_at(self, tokens: list[str], at: int) -> tuple[tuple[str, Any, str | None], int] | None:
        for width in range(min(self.longest_modifier, len(tokens) - at), 0, -1):
            phrase = " ".join(tokens[at:at + width])
            hit = self.modifiers.get(phrase)
            if hit is not None:
                return hit, width
            if width == 1 and _DIGITS.fullmatch(phrase):
                # 아주 긴 숫자는 int() 가 거부하므로(파이썬 자릿수 한도) 그냥 아주 큰 수로 본다. 어차피 max_count 로 잘린다.
                return ("count", int(phrase) if len(phrase) <= 9 else 10**9, None), 1
        return None

    def noun(self, kind: str) -> str:
        word = self.kind_words.get(kind)
        return _singular(word) if word else kind.replace("_", " ")


def _read(tokens: list[str], grammar: _Grammar, catalog: Catalog, scene: list) -> _Found | None:
    """한 후보의 읽기들: [호칭을 뗀 단어 문장] -> [통째 단어 문장] -> [통째 물건 문법]. 처음 exact, 없으면 처음 partial."""
    readings: list[tuple[list[str], str | None]] = []
    for words, actor in VOCATIVES:
        if len(tokens) > len(words) and tuple(tokens[:len(words)]) == words:
            readings.append((tokens[len(words):], actor))
            break
    readings.append((tokens, None))
    first: _Found | None = None
    for rest, vocative in readings:
        found = list(_word_sentences(rest, vocative, grammar, catalog, scene))
        if vocative is None:
            thing = grammar.thing(rest, catalog, article_ok=True)
            if thing is not None:
                found.append(_Found("exact" if thing.canonical else "partial",
                                    [_add(thing.kind, thing.count or 1, thing.attrs(), scene, [])],
                                    _object_sentence(thing), thing.word_ids))
        for candidate in found:
            if candidate.grade == "exact":
                return candidate
            first = first or candidate
    return first


def _word_sentences(tokens: list[str], vocative: str | None, grammar: _Grammar, catalog: Catalog, scene: list):
    literal = grammar.literals.get(" ".join(tokens))
    if literal is not None:
        word, index = literal
        ops = _present(word, None, vocative, catalog, scene)
        if ops is not None:
            yield _Found("exact" if index == 0 else "partial", ops, _sentence(word, None, None, grammar), (word.id,))
    for template in grammar.templates:
        head, tail = len(template.head), len(template.tail)
        if len(tokens) <= head + tail or tuple(tokens[:head]) != template.head or tuple(tokens[len(tokens) - tail:]) != template.tail:
            continue
        thing = grammar.thing(tokens[head:len(tokens) - tail], catalog, article_ok=not template.determiner)
        if thing is None:
            continue
        ops = _present(template.word, thing, vocative, catalog, scene)
        if ops is None:
            continue
        exact = (template.index == 0 or template.determiner) and thing.canonical
        yield _Found("exact" if exact else "partial", ops, _sentence(template.word, thing, template, grammar),
                     tuple(dict.fromkeys([template.word.id, *thing.word_ids])))


def _present(word: Word, thing: _Thing | None, vocative: str | None, catalog: Catalog, scene: list) -> list[dict] | None:
    """단어의 present 를 ops 로. 이 읽기가 안 되는 경우(음식 아닌 것 물기, 입을 수 없는 것 입기, 둘 이상 차기)는 None."""
    present = word.present
    if present["type"] == "mood":  # R4: 표정은 캐릭터 대상의 set
        face = present["face"]
        if vocative and face in catalog.actor_faces(vocative):
            targets = [vocative]
        else:
            targets = list(ACTORS) if present["actor"] == "all" else [present["actor"]]
        return [{"op": "set", "target": target, "face": face} for target in targets]

    actor, action = present["actor"], present["action"]
    if vocative and action in catalog.actor_actions(vocative):
        actor = vocative
    role = _role(present)
    need_kind, need = (thing.kind, thing) if thing and role == "needs" else (_default(present, "needs"), None)
    remove_kind, remove = (thing.kind, thing) if thing and role == "removes" else (_default(present, "removes"), None)

    ops: list[dict] = []
    act = {"op": "act", "actor": actor, "action": action}
    if need_kind:
        if action == "chomp" and not catalog.is_food(need_kind):
            return None  # 멍멍이는 음식만 문다 -> LLM 이 다른 걸 제안한다
        if action == "put_on" and not catalog.is_wearable(need_kind):
            return None
        if need and need.count is not None and need.count > 1:
            return None  # 동작의 대상은 하나다("kick the two balls")
        attrs = need.attrs() if need else {}
        target = _latest(scene, need_kind, attrs)
        if target is None:
            ops.append(_add(need_kind, 1, attrs, scene, ops))
            target = ops[-1]["id"]
        act["target"] = target
    ops.append(act)
    if remove_kind:
        attrs = remove.attrs() if remove else {}
        doomed = list(dict.fromkeys(entry["id"] for entry in reversed(scene) if _is(entry, remove_kind, attrs)))
        if remove and remove.count is not None:
            doomed = doomed[:remove.count]
        elif remove and not _said_plural(remove):
            doomed = doomed[:1]  # "throw out the egg": 말과 화면이 같도록 하나만. 복수로 말했을 때만 전부
        ops.extend({"op": "remove", "target": ident} for ident in doomed)  # 가장 최근 것부터. 4개 자르기는 match 가 한다
    return ops


def _role(present: dict) -> str:
    """`{object}` 자리의 물건이 무엇이 되나: removes 만 있는 단어면 치울 것, 아니면 동작의 대상(needs)."""
    return "removes" if "removes" in present and "needs" not in present else "needs"


def _default(present: dict, key: str) -> str | None:
    value = present.get(key)
    return value if isinstance(value, str) and value != PLACEHOLDER else None


def _is(entry: object, kind: str, attrs: dict[str, str]) -> bool:
    return (isinstance(entry, dict) and entry.get("kind") == kind and isinstance(entry.get("id"), str)
            and all(entry.get(key) == value for key, value in attrs.items()))


def _latest(scene: list, kind: str, attrs: dict[str, str]) -> str | None:
    """장면에서 가장 최근(목록의 뒤쪽)에 들어온, 말한 색·크기와 같은 그 물건의 id."""
    for entry in reversed(scene):
        if _is(entry, kind, attrs):
            return entry["id"]
    return None


def _add(kind: str, count: int, attrs: dict[str, str], scene: list, pending: list[dict]) -> dict:
    ids = next_ids(kind, count, scene, pending)
    op: dict[str, Any] = {"op": "add", "kind": kind}
    if count == 1:
        op["id"] = ids[0]
    else:
        op["ids"] = ids
    op["count"] = count
    op.update(attrs)
    return op


# ---------------------------------------------------------------- 대사

def _say(turn_id: int, sentence: str) -> str:
    body = " ".join(sentence.split()).rstrip(".!?").strip()
    say = f"{praise(turn_id)} {body}!" if body else praise(turn_id)
    return say if len(say.split()) <= MAX_SAY_WORDS else praise(turn_id)


def _sentence(word: Word, thing: _Thing | None, template: _Template | None, grammar: _Grammar) -> str:
    """단어 문장의 바른 영어. En 의 `{object}` 는 들어간 물건 말(없으면 needs 기본 물건)로 바꾼다.

    En 에 자리가 없는데 물건 자리를 썼으면("kick the egg" 인데 En 은 "Kick the ball") 관사가 있는 첫 틀로 말한다:
    아이가 실제로 움직인 물건을 말해 주어야 하기 때문이다.
    """
    en = word.en
    if thing is None:
        kind = _default(word.present, "needs") or _default(word.present, "removes")
        if PLACEHOLDER not in en:
            return en
        return en.replace(PLACEHOLDER, grammar.noun(kind)) if kind else _EMPTY_SLOT.sub("", en)
    phrase = _slot_phrase(thing, _role(word.present))
    if PLACEHOLDER in en:
        return en.replace(PLACEHOLDER, phrase)
    pattern = next((p for p in word.accept if _determiner_slot(p)), word.accept[template.index] if template else en)
    text = pattern.replace(PLACEHOLDER, phrase)
    return text[:1].upper() + text[1:]


def _determiner_slot(phrase: str) -> bool:
    tokens = phrase.split()
    if tokens.count(PLACEHOLDER) != 1:
        return False
    at = tokens.index(PLACEHOLDER)
    return at > 0 and tokens[at - 1] in DETERMINERS


def _slot_phrase(thing: _Thing, role: str) -> str:
    """틀 안의 물건 말. 대상(needs)은 언제나 하나(단수), 치울 것은 개수를 말했으면 그 수, 아니면 아이가 말한 그대로."""
    adjectives = [value for value in (thing.size, thing.color) if value]
    if role == "removes" and thing.count is None:
        return " ".join([*adjectives, thing.said])
    if (thing.count or 1) == 1:
        return " ".join([*adjectives, _singular(thing.word)])
    return " ".join([_number(thing.count), *adjectives, _plural(thing.word)])


def _object_sentence(thing: _Thing) -> str:
    """"A big red egg", "An apple", "Two red balls", "Socks"(한 켤레 말에는 관사가 없다)."""
    count = thing.count or 1
    adjectives = [value for value in (thing.size, thing.color) if value]
    if count == 1:
        body = " ".join([*adjectives, _singular(thing.word)])
        text = body if _pair_noun(thing.word) else f"{'an' if body[0] in 'aeiou' else 'a'} {body}"
    else:
        text = " ".join([_number(count), *adjectives, _plural(thing.word)])
    return text[:1].upper() + text[1:]


def _number(count: int) -> str:
    return NUMBER_WORDS[count - 1] if 1 <= count <= len(NUMBER_WORDS) else str(count)


def _singular(word: Word) -> str:
    return word.en.lower()


def _pair_noun(word: Word) -> bool:
    """"socks" 처럼 En 이 이미 복수이고 단어장에 단수("sock")가 따로 있는 말."""
    noun = _singular(word)
    return noun.endswith("s") and noun[:-1] in word.accept


def _said_plural(thing: _Thing) -> bool:
    """아이가 물건을 복수로 말했나("eggs"). 한 켤레 말("socks", "sock")은 언제나 하나로 본다."""
    return not _pair_noun(thing.word) and thing.said == _plural(thing.word)


def _plural(word: Word) -> str:
    noun = _singular(word)
    if _pair_noun(word):
        return noun
    for candidate in (noun + "es", noun[:-1] + "ies", noun + "s"):
        if candidate in word.accept:  # 단어장에 적힌 복수가 있으면 그것을 쓴다
            return candidate
    if noun.endswith(("s", "x", "z", "ch", "sh")):
        return noun + "es"
    if noun.endswith("y") and noun[-2:-1] not in ("a", "e", "i", "o", "u"):
        return noun[:-1] + "ies"
    return noun + "s"
