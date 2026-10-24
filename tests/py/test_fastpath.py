"""server/fastpath.py(작은 문법) 와 server/ids.py(서버가 정하는 id) 의 테스트.

fastpath 는 인식된 말을 LLM 없이 1ms 안에 장면 조작(ops)으로 바꾼다(스펙 5.6).
표 하나(브리프의 표 + 컨트롤러 판정 R3'·R4·R7·R8 + 경계 사례)로 "무슨 말 → 무슨 ops, exact/partial" 을 고정한다.
"""
from __future__ import annotations

import copy
import dataclasses
import re
import time
from pathlib import Path

import pytest

from server.catalog import load_catalog
from server.fastpath import FastMatch, match
from server.ids import next_ids
from server.text import has_hangul
from server.words import Word, WordBank, load_words

ROOT = Path(__file__).resolve().parents[2]
WORDS_DIR = ROOT / "content" / "words"
CATALOG_PATH = ROOT / "shared" / "catalog.json"

# 스키마(turn_reply.schema.json)의 englishText: 15단어 이하, 공백 하나
SAY_SHAPE = re.compile(r"\S+(?: \S+){0,14}")
OP_KEYS = {
    "add": {"op", "kind", "id", "ids", "count", "color", "size"},
    "act": {"op", "actor", "action", "target"},
    "set": {"op", "target", "face"},
    "remove": {"op", "target"},
}

EGG1 = {"id": "egg1", "kind": "egg"}
BALL1 = {"id": "ball1", "kind": "ball"}
TREE1 = {"id": "tree1", "kind": "tree"}
APPLE1 = {"id": "apple1", "kind": "apple"}
DINO = {"id": "dino", "kind": "dino", "state": "idle"}
CHOMP = {"id": "chomp", "kind": "chomp", "state": "idle"}


def toy(i: int) -> dict:
    return {"id": f"toys{i + 1}", "kind": "toys"}


def item(id_: str, kind: str, **attrs) -> dict:
    return {"id": id_, "kind": kind, **attrs}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_PATH)


@pytest.fixture(scope="module")
def bank(catalog) -> WordBank:
    return load_words(WORDS_DIR, catalog)


# ---------------------------------------------------------------- 표 비교 도우미

def expected_op(spec: tuple) -> dict:
    """표의 튜플 -> 비교할 op 조각. ("add", kind, {attrs}?) ("act", actor, action, target?) ("set", target, {attrs}) ("remove", target)."""
    op, *rest = spec
    attrs = rest.pop() if rest and isinstance(rest[-1], dict) else {}
    if op == "add":
        (kind,) = rest
        return {"op": "add", "kind": kind, **attrs}
    if op == "act":
        actor, action, *target = rest
        return {"op": "act", "actor": actor, "action": action, **({"target": target[0]} if target else {}), **attrs}
    if op in ("set", "remove"):
        (target,) = rest
        return {"op": op, "target": target, **attrs}
    raise AssertionError(f"bad table op {spec!r}")


def assert_ops(ops: list[dict], specs: list[tuple]) -> None:
    assert len(ops) == len(specs), ops
    added: list[str] = []
    for op, spec in zip(ops, specs):
        want = expected_op(spec)
        assert {k: op.get(k) for k in want} == want, (op, spec)
        assert set(op) <= OP_KEYS[op["op"]], op
        if op["op"] == "add":
            count = op["count"]
            assert 1 <= count <= 5
            ids = [op["id"]] if count == 1 else op["ids"]
            assert ("id" in op) == (count == 1) and ("ids" in op) == (count > 1), op
            assert len(ids) == count
            added.extend(ids)
        if op["op"] == "act" and len(spec) == 3:  # 표에 target 이 없는 act
            if added:
                assert op["target"] == added[-1], op  # add 가 새로 만든 id 를 가리킨다
            else:
                assert "target" not in op, op


def assert_say_ok(say: str) -> None:
    assert SAY_SHAPE.fullmatch(say), say
    assert not has_hangul(say) and "<" not in say and "{" not in say


CASES = [
    # ---- 브리프의 표 (R7: "dino hide" 는 exact)
    (["kick the ball"], [], "exact", [("add", "ball"), ("act", "dino", "kick")]),
    (["kick ball"], [], "partial", [("add", "ball"), ("act", "dino", "kick")]),
    (["kick the egg"], [EGG1], "exact", [("act", "dino", "kick", "egg1")]),
    (["big red egg"], [], "exact", [("add", "egg", {"size": "big", "color": "red", "count": 1})]),
    (["ten eggs"], [], "exact", [("add", "egg", {"count": 5})]),
    (["chomp jump"], [], "exact", [("act", "chomp", "jump")]),
    (["dino hide"], [], "exact", [("act", "dino", "hide")]),
    (["chomp the egg"], [EGG1], None, None),  # 음식이 아니라 매치 안 함
    (["빨간 알", "red egg"], [], "exact", [("add", "egg", {"color": "red"})]),  # 한글 후보 건너뛰기
    (["what is your name"], [], None, None),
    # ---- 정규화, 이미 있는 물건, 가장 최근 물건
    (["Kick the ball!"], [], "exact", [("add", "ball"), ("act", "dino", "kick")]),
    (["kick the ball"], [DINO, BALL1], "exact", [("act", "dino", "kick", "ball1")]),
    (["kick the ball"], [item("ball1", "ball"), item("ball2", "ball")], "exact", [("act", "dino", "kick", "ball2")]),
    (["kick the ball"], [EGG1], "exact", [("add", "ball", {"id": "ball1"}), ("act", "dino", "kick", "ball1")]),
    (["kick"], [], "partial", [("add", "ball"), ("act", "dino", "kick")]),
    (["kick a ball"], [], "partial", [("add", "ball"), ("act", "dino", "kick")]),
    # ---- {object} 자리에 물건 문법
    (["kick the big red ball"], [], "exact",
     [("add", "ball", {"size": "big", "color": "red", "count": 1}), ("act", "dino", "kick")]),
    (["kick the red ball"], [item("ball1", "ball", color="blue")], "exact",
     [("add", "ball", {"id": "ball2", "color": "red"}), ("act", "dino", "kick", "ball2")]),
    (["kick the red ball"], [item("ball1", "ball", color="red"), item("ball2", "ball", color="blue")], "exact",
     [("act", "dino", "kick", "ball1")]),
    (["kick the two balls"], [], None, None),  # 한 번에 하나만 찬다
    (["kick the the ball"], [], None, None),
    (["kick the a ball"], [], None, None),  # 관사 뒤 관사
    (["kick the red big ball"], [], "partial", [("add", "ball", {"size": "big", "color": "red"}), ("act", "dino", "kick")]),
    # ---- 물건 문법만
    (["two balls"], [], "exact", [("add", "ball", {"count": 2, "ids": ["ball1", "ball2"]})]),
    (["2 balls"], [BALL1], "exact", [("add", "ball", {"count": 2, "ids": ["ball2", "ball3"]})]),
    (["100 eggs"], [], "exact", [("add", "egg", {"count": 5})]),
    (["twenty eggs"], [], "exact", [("add", "egg", {"count": 5})]),
    (["a big red egg"], [], "exact", [("add", "egg", {"size": "big", "color": "red", "count": 1})]),
    (["an apple"], [], "exact", [("add", "apple", {"count": 1, "id": "apple1"})]),
    (["egg"], [EGG1], "exact", [("add", "egg", {"count": 1, "id": "egg2"})]),
    (["eggs"], [], "exact", [("add", "egg", {"count": 1})]),
    (["red apple"], [], "exact", [("add", "apple", {"count": 1})]),  # 사과는 색을 못 가진다 -> 버림(아래에서 키 없음 확인)
    (["big socks"], [], "exact", [("add", "socks", {"count": 1})]),  # 양말은 크기를 못 가진다
    (["big big big egg"], [], "partial", [("add", "egg", {"size": "big", "count": 1})]),
    (["red big egg"], [], "partial", [("add", "egg", {"size": "big", "color": "red"})]),
    (["big small egg"], [], None, None),
    (["red blue egg"], [], None, None),
    (["zero eggs"], [], None, None),
    (["0 eggs"], [], None, None),
    (["red"], [], None, None),  # 꾸미는 말 혼자서는 아무것도 만들지 않는다
    (["big"], [], None, None),
    (["a"], [], None, None),
    (["red egg big"], [], None, None),
    (["chomp apple"], [], None, None),  # 호칭 뒤에는 동작만: 사과를 만들지 않는다
    (["dino two balls"], [], None, None),
    # ---- 멍멍이는 음식만 문다
    (["chomp the apple"], [], "exact", [("add", "apple"), ("act", "chomp", "chomp")]),
    (["eat the apple"], [APPLE1], "exact", [("act", "chomp", "chomp", "apple1")]),
    (["chomp the big red apple"], [], "exact", [("add", "apple", {"count": 1}), ("act", "chomp", "chomp")]),
    (["chomp"], [], "partial", [("add", "apple"), ("act", "chomp", "chomp")]),
    (["eat the ball"], [BALL1], None, None),
    (["chomp the egg"], [], None, None),
    (["dino chomp the apple"], [], "exact", [("add", "apple"), ("act", "chomp", "chomp")]),
    # ---- 호칭
    (["Hey Chomp, jump!"], [], "exact", [("act", "chomp", "jump")]),
    (["hey dino hide"], [], "exact", [("act", "dino", "hide")]),
    (["dino jump"], [], "exact", [("act", "chomp", "jump")]),  # 공룡은 jump 를 못 한다 -> 호칭 무시
    (["chomp kick the ball"], [], "exact", [("add", "ball"), ("act", "dino", "kick")]),  # 멍멍이는 못 찬다
    (["chomp dance"], [], "partial", [("act", "dino", "dance")]),
    (["hey chomp"], [], None, None),
    (["dino"], [], None, None),
    # ---- 표정(R4)
    (["happy"], [], "exact", [("set", "chomp", {"face": "happy"}), ("set", "dino", {"face": "happy"})]),
    (["good job"], [], "exact", [("set", "chomp", {"face": "happy"}), ("set", "dino", {"face": "happy"})]),
    (["chomp sad"], [], "exact", [("set", "chomp", {"face": "sad"})]),
    (["dino happy"], [], "exact", [("set", "dino", {"face": "happy"})]),
    # ---- 치우기(removes)
    (["clean up your toys"], [toy(0), toy(1)], "exact", [("act", "dino", "clean_up"), ("remove", "toys2"), ("remove", "toys1")]),
    (["clean up your toys"], [], "exact", [("act", "dino", "clean_up")]),
    (["clean up"], [toy(0)], "partial", [("act", "dino", "clean_up"), ("remove", "toys1")]),
    (["throw out the egg"], [EGG1], "exact", [("act", "dino", "throw_out"), ("remove", "egg1")]),
    (["throw out the eggs"], [EGG1, item("egg2", "egg"), BALL1], "exact",
     [("act", "dino", "throw_out"), ("remove", "egg2"), ("remove", "egg1")]),
    (["throw out the red egg"], [item("egg1", "egg", color="red"), item("egg2", "egg", color="blue")], "exact",
     [("act", "dino", "throw_out"), ("remove", "egg1")]),
    (["throw out the two eggs"], [EGG1, item("egg2", "egg"), item("egg3", "egg")], "exact",
     [("act", "dino", "throw_out"), ("remove", "egg3"), ("remove", "egg2")]),
    (["throw out the egg"], [], "exact", [("act", "dino", "throw_out")]),
    # 단수로 말하면 가장 최근 것 하나만, 복수로 말하면 전부(4개 자르기 안에서)
    (["throw out the egg"], [EGG1, item("egg2", "egg"), item("egg3", "egg")], "exact",
     [("act", "dino", "throw_out"), ("remove", "egg3")]),
    (["throw out the eggs"], [EGG1, item("egg2", "egg"), item("egg3", "egg")], "exact",
     [("act", "dino", "throw_out"), ("remove", "egg3"), ("remove", "egg2"), ("remove", "egg1")]),
    (["throw out the ball"], [BALL1, item("ball2", "ball")], "exact", [("act", "dino", "throw_out"), ("remove", "ball2")]),
    (["throw out the red apple"], [APPLE1, item("apple2", "apple")], "exact",
     [("act", "dino", "throw_out"), ("remove", "apple2")]),
    (["throw out the socks"], [item("socks1", "socks"), item("socks2", "socks")], "exact",
     [("act", "dino", "throw_out"), ("remove", "socks2")]),  # 한 켤레 말은 하나
    (["throw out the sock"], [item("socks1", "socks"), item("socks2", "socks")], "exact",
     [("act", "dino", "throw_out"), ("remove", "socks2")]),
    (["throw out"], [EGG1], "partial", [("act", "dino", "throw_out")]),
    # ---- 입기, 오르기
    (["put on your socks"], [], "exact", [("add", "socks"), ("act", "dino", "put_on")]),
    (["put on socks"], [], "partial", [("add", "socks"), ("act", "dino", "put_on")]),
    (["put on your red socks"], [], "exact", [("add", "socks", {"color": "red"}), ("act", "dino", "put_on")]),
    (["put on your egg"], [], None, None),  # 입을 수 없는 물건
    (["climb up"], [], "exact", [("add", "tree"), ("act", "dino", "climb_up")]),
    (["climb up the tree"], [TREE1], "exact", [("act", "dino", "climb_up", "tree1")]),
    (["climb"], [TREE1], "partial", [("act", "dino", "climb_up", "tree1")]),
    # ---- 그 밖의 문장
    (["Let's dance!"], [], "exact", [("act", "dino", "dance")]),
    (["turn-around"], [], "exact", [("act", "dino", "turn_around")]),
    (["thumb up"], [], "partial", [("act", "dino", "thumbs_up")]),
    # ---- 글자 그대로의 중괄호는 패턴과 맞지 않는다
    (["kick the {object}"], [], None, None),
    (["throw out the {object}"], [EGG1], None, None),
    (["kick the ｛object｝"], [], None, None),
    (["kick the {ball}"], [], None, None),
    (["{jump}"], [], None, None),
    # ---- 후보 고르기
    (["what is your name", "jump"], [], "exact", [("act", "chomp", "jump")]),
    (["jump", "kick the ball"], [], "exact", [("act", "chomp", "jump")]),
    (["chomp the egg", "chomp the apple"], [EGG1], "exact", [("add", "apple"), ("act", "chomp", "chomp")]),
    (["kick the 공"], [], None, None),
    (["", "   ", "uh"], [], None, None),
    ([], [], None, None),
]


@pytest.mark.parametrize("alts,scene,grade,ops", CASES, ids=[" | ".join(c[0]) or "<none>" for c in CASES])
def test_fastpath_table(bank, catalog, alts, scene, grade, ops):
    m = match(alts, bank, catalog, scene, turn_id=0)
    if grade is None:
        assert m is None, m
        return
    assert m is not None
    assert m.grade == grade
    assert_ops(m.ops, ops)
    assert_say_ok(m.say)
    assert m.word_ids and all(isinstance(w, str) for w in m.word_ids)


def test_ops_capped_at_four(bank, catalog):
    m = match(["clean up your toys"], bank, catalog, [toy(i) for i in range(6)], turn_id=1)
    assert len(m.ops) <= 4
    assert m.ops == [
        {"op": "act", "actor": "dino", "action": "clean_up"},
        {"op": "remove", "target": "toys6"},
        {"op": "remove", "target": "toys5"},
        {"op": "remove", "target": "toys4"},
    ]


def test_say_has_no_korean_and_is_short(bank, catalog):
    m = match(["big red egg"], bank, catalog, [], turn_id=0)
    assert m.say == "Good job! A big red egg!"


def test_next_ids_skips_existing_and_pending():
    assert next_ids("egg", 2, [{"id": "egg1"}, {"id": "egg3"}], [{"op": "add", "id": "egg4"}]) == ["egg5", "egg6"]


# ---------------------------------------------------------------- 정확한 ops 모양

def test_kick_the_ball_exact_ops(bank, catalog):
    m = match(["kick the ball"], bank, catalog, [DINO, CHOMP], turn_id=0)
    assert m == FastMatch(
        ops=[{"op": "add", "kind": "ball", "id": "ball1", "count": 1},
             {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}],
        say="Good job! Kick the ball!", grade="exact", word_ids=["kick-the-ball"])


def test_object_phrase_exact_ops(bank, catalog):
    m = match(["two red balls"], bank, catalog, [BALL1], turn_id=2)
    assert m.ops == [{"op": "add", "kind": "ball", "ids": ["ball2", "ball3"], "count": 2, "color": "red"}]
    assert m.say == "Thumbs up! Two red balls!"
    assert m.word_ids == ["two", "red", "ball"]


def test_dropped_attrs_leave_no_keys(bank, catalog):
    assert match(["big red apple"], bank, catalog, [], turn_id=0).ops == [{"op": "add", "kind": "apple", "id": "apple1", "count": 1}]
    assert match(["small socks"], bank, catalog, [], turn_id=0).ops == [{"op": "add", "kind": "socks", "id": "socks1", "count": 1}]
    eat = match(["chomp the big red apple"], bank, catalog, [], turn_id=0)
    assert eat.ops[0] == {"op": "add", "kind": "apple", "id": "apple1", "count": 1}


def test_mood_ops_r4(bank, catalog):
    assert match(["sad"], bank, catalog, [], turn_id=0).ops == [
        {"op": "set", "target": "chomp", "face": "sad"}, {"op": "set", "target": "dino", "face": "sad"}]


def test_add_never_has_place(bank, catalog):
    for text in ("big red egg", "kick the ball", "three eggs", "put on your socks"):
        for op in match([text], bank, catalog, [], turn_id=0).ops:
            assert "place" not in op


# ---------------------------------------------------------------- 대사(say)

@pytest.mark.parametrize("alts,scene,say", [
    (["big red egg"], [], "Good job! A big red egg!"),
    (["a big red egg"], [], "Good job! A big red egg!"),
    (["red big egg"], [], "Good job! A big red egg!"),
    (["two red balls"], [], "Good job! Two red balls!"),
    (["two ball"], [], "Good job! Two balls!"),
    (["ten eggs"], [], "Good job! Five eggs!"),
    (["eggs"], [], "Good job! An egg!"),
    (["apple"], [], "Good job! An apple!"),
    (["red apple"], [], "Good job! An apple!"),  # 색을 버렸으니 말도 화면과 같게
    (["little egg"], [], "Good job! A small egg!"),
    (["yellow egg"], [], "Good job! A yellow egg!"),
    (["socks"], [], "Good job! Socks!"),
    (["two green socks"], [], "Good job! Two green socks!"),
    (["kick the ball"], [], "Good job! Kick the ball!"),
    (["kick ball"], [], "Good job! Kick the ball!"),
    (["kick"], [], "Good job! Kick the ball!"),
    (["kick the egg"], [EGG1], "Good job! Kick the egg!"),
    (["kick the big red ball"], [], "Good job! Kick the big red ball!"),
    (["chomp the apple"], [], "Good job! Chomp the apple!"),
    (["eat the apple"], [], "Good job! Chomp the apple!"),
    (["chomp"], [], "Good job! Chomp the apple!"),
    (["throw out the egg"], [EGG1], "Good job! Throw out the egg!"),
    (["throw out the eggs"], [EGG1], "Good job! Throw out the eggs!"),
    (["throw out the two eggs"], [EGG1], "Good job! Throw out the two eggs!"),
    (["throw out"], [EGG1], "Good job! Throw out!"),
    (["chomp jump"], [], "Good job! Jump!"),
    (["lets dance"], [], "Good job! Let's dance!"),
    (["put on your socks"], [], "Good job! Put on your socks!"),
    (["put on your red socks"], [], "Good job! Put on your red socks!"),
    (["climb up"], [], "Good job! Climb up!"),
    (["climb up the tree"], [], "Good job! Climb up the tree!"),
    (["happy"], [], "Good job! Happy!"),
])
def test_say_text(bank, catalog, alts, scene, say):
    assert match(alts, bank, catalog, scene, turn_id=0).say == say


def test_say_praise_follows_turn_id(bank, catalog):
    says = [match(["jump"], bank, catalog, [], turn_id=t).say for t in range(6)]
    assert says == ["Good job! Jump!", "Excellent! Jump!", "Thumbs up! Jump!", "Wow! Jump!", "Well done! Jump!", "Good job! Jump!"]


def test_every_word_says_short_english(bank, catalog):
    """단어장의 모든 accept(자리에는 egg) 를 넣어 봐도 say 는 한글 없는 15단어 이하."""
    seen = 0
    for word in bank.words:
        for phrase in word.accept:
            for text in (phrase.replace("{object}", "big red egg"), phrase.replace("{object}", "apple")):
                for scene in ([], [EGG1, BALL1, TREE1, APPLE1, toy(0)]):
                    m = match([text], bank, catalog, scene, turn_id=3)
                    if m is not None:
                        seen += 1
                        assert_say_ok(m.say)
                        assert len(m.ops) <= catalog.max_ops
    assert seen > 60


# ---------------------------------------------------------------- 레벨, 카탈로그 한도

def test_level_limits_words(bank, catalog):
    assert match(["kick the ball"], bank, catalog, [], turn_id=0, level=1).grade == "exact"
    assert match(["big red egg"], bank, catalog, [], turn_id=0, level=1) is None
    assert match(["kick the egg"], bank, catalog, [EGG1], turn_id=0, level=1) is None
    assert match(["happy"], bank, catalog, [], turn_id=0, level=1) is None
    assert match(["jump"], bank, catalog, [], turn_id=0, level=0) is None


def test_count_clamped_to_catalog_max_count(bank, catalog):
    small = dataclasses.replace(catalog, max_count=3)
    m = match(["five eggs"], bank, small, [], turn_id=0)
    assert m.ops == [{"op": "add", "kind": "egg", "ids": ["egg1", "egg2", "egg3"], "count": 3}]
    assert m.say == "Good job! Three eggs!"


def test_ops_capped_at_catalog_max_ops(bank, catalog):
    one = dataclasses.replace(catalog, max_ops=1)
    assert match(["kick the ball"], bank, one, [], turn_id=0).ops == [{"op": "add", "kind": "ball", "id": "ball1", "count": 1}]
    assert len(match(["happy"], bank, one, [], turn_id=0).ops) == 1


# ---------------------------------------------------------------- exact/partial 판정(R3'): 관사가 있는 틀

def _act_word(id_, en, accept, **present):
    return Word(id=id_, en=en, ko="차", accept=tuple(accept), level=1, category="action", source="core",
                present={"type": "act", "actor": "dino", "action": "kick", **present})


def _sprite(id_, accept, kind, level=1):
    return Word(id=id_, en=accept[0].capitalize(), ko="물건", accept=tuple(accept), level=level,
                category="object", source="core", present={"type": "sprite", "kind": kind})


def _attr(id_, slot, value):
    return Word(id=id_, en=id_.capitalize(), ko="꾸밈", accept=(id_,), level=1, category=slot, source="core",
                present={"type": "attr", "slot": slot, "value": value})


def test_determiner_templates_are_exact(catalog):
    bank = WordBank([
        _act_word("kick-it", "Kick it", ["kick it", "kick my {object}", "kick an {object}", "kick {object} now"], needs="ball"),
        _sprite("egg", ["egg", "eggs"], "egg"),
        _sprite("teddy", ["teddy bear", "teddy bears"], "toys"),
        _attr("big", "size", "big"),
    ])
    assert match(["kick it"], bank, catalog, [], turn_id=0).grade == "exact"
    assert match(["kick my egg"], bank, catalog, [], turn_id=0).grade == "exact"
    assert match(["kick an egg"], bank, catalog, [], turn_id=0).grade == "exact"
    now = match(["kick egg now"], bank, catalog, [], turn_id=0)
    assert now.grade == "partial" and now.say == "Good job! Kick my egg!"  # 관사 있는 첫 틀로 다시 말해 준다
    bear = match(["kick my big teddy bear"], bank, catalog, [], turn_id=0)  # 여러 낱말 물건, 크기는 버림
    assert bear.ops[0] == {"op": "add", "kind": "toys", "id": "toys1", "count": 1}
    assert bear.word_ids == ["kick-it", "big", "teddy"]  # 버린 꾸밈말도 아이가 쓴 단어다


def _chomp_word(id_, accept):
    return Word(id=id_, en=accept[0].capitalize(), ko="자", accept=tuple(accept), level=1, category="action",
                source="core", present={"type": "act", "actor": "chomp", "action": "sleep"})


def test_exact_reading_beats_earlier_partial_reading(catalog):
    """호칭을 뗀 읽기("sit", partial)가 먼저 나와도 통째 읽기("chomp sit", exact)가 이긴다."""
    bank = WordBank([_chomp_word("sit", ["sit down", "sit"]), _chomp_word("chomp-sit", ["chomp sit"])])
    m = match(["chomp sit"], bank, catalog, [], turn_id=0)
    assert m.grade == "exact" and m.word_ids == ["chomp-sit"]
    assert match(["chomp sit down"], bank, catalog, [], turn_id=0).word_ids == ["sit"]


def test_hangul_candidate_skipped_even_if_a_pattern_would_match(catalog):
    """규칙 1: 한글이 든 후보는 맞춰 보지도 않는다(로더를 거치지 않은 단어장으로 확인)."""
    bank = WordBank([_chomp_word("jump-ko", ["jump", "점프"])])
    assert match(["점프"], bank, catalog, [], turn_id=0) is None
    assert match(["점프", "jump"], bank, catalog, [], turn_id=0).grade == "exact"


def test_two_placeholders_never_match(catalog):
    bank = WordBank([_act_word("odd", "Odd", ["odd", "kick {object} {object}"], needs="ball"), _sprite("egg", ["egg"], "egg")])
    assert match(["kick egg egg"], bank, catalog, [], turn_id=0) is None


# ---------------------------------------------------------------- 아이 말 앞뒤의 군소리("um", "uh")

FILLER_CASES = [
    # (들린 말, 군소리를 뺀 말): 둘의 결과(ops, 대사, 판정, 단어)가 똑같아야 한다
    ("um kick the ball", "kick the ball"),
    ("kick the ball uh", "kick the ball"),
    ("uh um big red egg", "big red egg"),
    ("hmm kick ball", "kick ball"),  # partial 은 partial 그대로
    ("kick ball um", "kick ball"),
    ("er jump ah", "jump"),
    ("Um, hey Chomp, jump!", "hey chomp jump"),
    ("um throw out the egg hmm", "throw out the egg"),
    ("uh two balls", "two balls"),
]


@pytest.mark.parametrize("said, plain", FILLER_CASES, ids=[c[0] for c in FILLER_CASES])
def test_leading_and_trailing_fillers_are_ignored(bank, catalog, said, plain):
    scene = [EGG1]
    want = match([plain], bank, catalog, scene, turn_id=3)
    assert want is not None
    assert match([said], bank, catalog, scene, turn_id=3) == want  # 뗀 군소리는 판정을 바꾸지 않는다


def test_a_filler_in_the_middle_is_kept(bank, catalog):
    assert match(["kick uh the ball"], bank, catalog, [], turn_id=0) is None


def test_fillers_alone_still_match_nothing(bank, catalog):
    assert match(["um uh", "hmm", "ah er"], bank, catalog, [], turn_id=0) is None


def test_a_word_made_of_fillers_matches_as_said(catalog):
    # 단어장에 "uh oh" 같은 말이 들어와도(G4) 그대로 맞는다: 군소리는 통째로 맞는 읽기가 없을 때만 뗀다
    bank = WordBank([_chomp_word("uh-oh", ["uh oh"])])
    found = match(["uh oh"], bank, catalog, [], turn_id=0)
    assert found is not None and found.grade == "exact" and found.word_ids == ["uh-oh"]


# ---------------------------------------------------------------- 입력과 장면을 안전하게

def test_non_string_alternatives_skipped(bank, catalog):
    m = match([None, 3, {"t": "jump"}, "jump"], bank, catalog, [], turn_id=0)
    assert m.ops == [{"op": "act", "actor": "chomp", "action": "jump"}]


def test_bare_string_is_one_alternative(bank, catalog):
    assert match("kick the ball", bank, catalog, [], turn_id=0).grade == "exact"


def test_junk_scene_entries_ignored(bank, catalog):
    scene = [None, "ball9", {"kind": "ball"}, {"id": 7, "kind": "ball"}, {"id": "ball4", "kind": "ball"}, {"id": "x", "kind": ["ball"]}]
    m = match(["kick the ball"], bank, catalog, scene, turn_id=0)
    assert m.ops == [{"op": "act", "actor": "dino", "action": "kick", "target": "ball4"}]


def test_match_does_not_mutate_inputs(bank, catalog):
    scene = [EGG1, toy(0), toy(1)]
    before = copy.deepcopy(scene)
    words_before = copy.deepcopy([w.present for w in bank.words])
    for text in ("clean up your toys", "kick the egg", "big red egg", "happy", "throw out the egg"):
        match([text], bank, catalog, scene, turn_id=0)
    assert scene == before
    assert [w.present for w in bank.words] == words_before


def test_fast_match_is_frozen(bank, catalog):
    m = match(["jump"], bank, catalog, [], turn_id=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.grade = "partial"


def test_match_is_fast(bank, catalog):
    """스펙: fastpath 는 0.5초 목표, 이 모듈은 1ms 안. 섞인 입력 한 번에 평균 1ms 를 넉넉히 밑돌아야 한다."""
    inputs = [["kick the ball"], ["big red egg"], ["what is your name"], ["빨간 알", "chomp the egg", "two balls"],
              ["clean up your toys"], ["hey chomp jump"], ["throw out the red eggs"]]
    scene = [DINO, CHOMP, EGG1, BALL1, TREE1] + [toy(i) for i in range(10)]
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(30):
            for alts in inputs:
                match(alts, bank, catalog, scene, turn_id=0)
        best = min(best, (time.perf_counter() - start) / (30 * len(inputs)))
    assert best < 0.001, f"{best * 1000:.3f} ms per match"


# ---------------------------------------------------------------- next_ids

def test_next_ids_empty_scene():
    assert next_ids("egg", 1, [], []) == ["egg1"]


def test_next_ids_only_counts_exact_kind_prefix():
    scene = [{"id": "eggs9"}, {"id": "ball7"}, {"id": "egg"}, {"id": "dino"}, {"id": "xegg5"}, {"id": "egg2x"}]
    assert next_ids("egg", 1, scene, []) == ["egg1"]


def test_next_ids_reads_pending_ids_lists():
    assert next_ids("ball", 1, [{"id": "ball1"}], [{"op": "add", "ids": ["ball2", "ball3"]}]) == ["ball4"]


def test_next_ids_tolerates_junk():
    scene = [None, "egg5", {"id": 5}, {"id": ["egg9"]}, {"kind": "egg"}]
    pending = [{"ids": "egg8"}, {"ids": [None, "egg2", 7]}, "egg9", {"op": "add"}]
    assert next_ids("egg", 1, scene, pending) == ["egg3"]


def test_next_ids_leading_zeros_and_unicode_digits():
    assert next_ids("egg", 1, [{"id": "egg007"}, {"id": "egg٣٣"}], []) == ["egg8"]


def test_next_ids_non_positive_count():
    assert next_ids("egg", 0, [{"id": "egg1"}], []) == []
    assert next_ids("egg", -2, [], []) == []
