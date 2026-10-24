"""content/words/*.json 의 단어장과 server/words.py(로더)의 테스트.

진짜 단어장은 Task 10 표(슈니비니 동작 + 멍멍이 명령 + 인사, 레벨 2 맛보기)와 어긋나지 않는지 보고,
로더는 깨진 단어를 "어느 파일의 어느 단어, 어느 키가 문제인지" 담은 ValueError 로 거부하는지 본다.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
from pathlib import Path

import pytest

from server.catalog import load_catalog
from server.text import has_hangul, normalize
from server.words import MAX_LEVEL, SOURCES, Word, WordBank, load_words

ROOT = Path(__file__).resolve().parents[2]
WORDS_DIR = ROOT / "content" / "words"
CATALOG_PATH = ROOT / "shared" / "catalog.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_PATH)


@pytest.fixture(scope="module")
def bank(catalog) -> WordBank:
    return load_words(WORDS_DIR, catalog)


# ---------------------------------------------------------------- 브리프의 테스트

def test_bank_loads_and_counts():
    bank = load_words(WORDS_DIR, load_catalog(CATALOG_PATH))
    assert len(bank.words) >= 45 and len(bank.up_to(1)) >= 27
    assert bank.attrs("color")["red"] == "red" and bank.attrs("count")["3"] == 3
    assert bank.objects()["balls"] == "ball"


def test_unknown_action_rejected(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps([{"id": "x", "en": "X", "ko": "엑스", "accept": ["x"], "level": 1, "category": "action", "source": "core", "present": {"type": "act", "actor": "dino", "action": "fly"}}]), encoding="utf-8")
    with pytest.raises(ValueError, match="fly"):
        load_words(tmp_path, load_catalog(CATALOG_PATH))


def test_every_word_has_korean_meaning():
    bank = load_words(WORDS_DIR, load_catalog(CATALOG_PATH))
    assert all(re.search(r"[가-힣]", w.ko) for w in bank.words)


# ---------------------------------------------------------------- 진짜 단어장: 표와 같은가

def act(actor, action, **extra):
    return {"type": "act", "actor": actor, "action": action, **extra}


def word(id, en, accept, level, category, source, present, ko=None):
    return {"id": id, "en": en, "accept": accept, "level": level, "category": category,
            "source": source, "present": present, "ko": ko}


# Task 10 표 그대로. accept 의 첫 칸은 언제나 en 을 normalize 한 것이다(표에 다르게 적힌 줄은 그 칸을 앞으로 옮겼다).
TABLE = [
    word("turn-around", "Turn around", ["turn around", "turn"], 1, "action", "shunibini-A", act("dino", "turn_around"), "뒤로 돌아"),
    word("walk", "Walk", ["walk", "walking"], 1, "action", "shunibini-A", act("dino", "walk"), "걸어"),
    word("hide", "Hide", ["hide", "peekaboo"], 1, "action", "shunibini-A", act("chomp", "hide"), "숨어"),
    word("kick-the-ball", "Kick the ball", ["kick the ball", "kick the {object}", "kick {object}", "kick"], 1, "action", "shunibini-A",
         act("dino", "kick", needs="ball"), "공을 차"),
    word("stretch-your-arms", "Stretch your arms", ["stretch your arms", "stretch arms", "stretch"], 1, "action", "shunibini-A",
         act("dino", "stretch_arms"), "팔을 쭉 펴"),
    word("fold-your-arms", "Fold your arms", ["fold your arms", "fold arms"], 1, "action", "shunibini-A", act("dino", "fold_arms"), "팔짱 껴"),
    word("thumbs-up", "Thumbs up", ["thumbs up", "thumb up"], 1, "action", "shunibini-A", act("dino", "thumbs_up"), "엄지 척"),
    word("climb-up", "Climb up", ["climb up", "climb up the {object}", "climb"], 1, "action", "shunibini-B",
         act("dino", "climb_up", needs="tree"), "올라가"),
    word("yawn", "Yawn", ["yawn"], 1, "action", "shunibini-B", act("dino", "yawn"), "하품해"),
    word("sneeze", "Sneeze", ["sneeze", "achoo"], 1, "action", "shunibini-B", act("dino", "sneeze"), "재채기해"),
    word("take-a-deep-breath", "Take a deep breath", ["take a deep breath", "deep breath"], 1, "action", "shunibini-B",
         act("dino", "deep_breath"), "숨을 크게 쉬어"),
    word("clean-up-your-toys", "Clean up your toys", ["clean up your toys", "clean up"], 1, "action", "shunibini-A",
         act("dino", "clean_up", removes="toys"), "장난감 정리해"),
    word("throw-out", "Throw out the {object}", ["throw out the {object}", "throw out"], 1, "action", "shunibini-B",
         act("dino", "throw_out", removes="{object}"), "버려"),
    word("put-on-your-socks", "Put on your socks", ["put on your socks", "put on your {object}", "put on {object}"], 1, "action", "shunibini-B",
         act("dino", "put_on", needs="socks"), "양말 신어"),
    word("wash-your-hands", "Wash your hands", ["wash your hands", "wash hands"], 1, "action", "shunibini-B", act("dino", "wash_hands"), "손을 씻어"),
    word("go-to-bed", "Go to bed", ["go to bed"], 1, "action", "shunibini-B", act("dino", "go_to_bed"), "자러 가"),
    word("jump", "Jump", ["jump", "chomp jump"], 1, "action", "core", act("chomp", "jump"), "뛰어"),
    word("chomp", "Chomp the {object}", ["chomp the {object}", "eat the {object}", "chomp"], 1, "action", "core",
         act("chomp", "chomp", needs="apple"), "덥석 먹어"),
    word("bark", "Bark", ["bark", "woof"], 1, "action", "core", act("chomp", "bark"), "멍멍 짖어"),
    word("spin", "Spin", ["spin", "spin around"], 1, "action", "core", act("chomp", "spin"), "빙글빙글 돌아"),
    word("sleep", "Sleep", ["sleep"], 1, "action", "core", act("chomp", "sleep"), "쿨쿨 자"),
    word("hello", "Hello", ["hello", "hi", "hello chomp"], 1, "social", "core", act("chomp", "bark"), "안녕"),
    word("good-job", "Good job", ["good job"], 1, "social", "core", {"type": "mood", "actor": "all", "face": "happy"}, "잘했어"),
    word("give-me-five", "Give me five", ["give me five", "high five"], 1, "social", "core", act("dino", "high_five"), "하이파이브"),
    word("lets-clap", "Let's clap", ["lets clap", "clap"], 1, "social", "core", act("dino", "clap"), "박수 치자"),
    word("lets-dance", "Let's dance", ["lets dance", "dance"], 1, "social", "core", act("dino", "dance"), "춤추자"),
    word("good-night", "Good night", ["good night"], 1, "social", "core", act("chomp", "sleep"), "잘 자"),
    word("happy", "Happy", ["happy"], 2, "emotion", "core", {"type": "mood", "actor": "all", "face": "happy"}, "기뻐"),
    word("sad", "Sad", ["sad"], 2, "emotion", "core", {"type": "mood", "actor": "all", "face": "sad"}, "슬퍼"),
    word("red", "Red", ["red"], 2, "color", "core", {"type": "attr", "slot": "color", "value": "red"}, "빨강"),
    word("blue", "Blue", ["blue"], 2, "color", "core", {"type": "attr", "slot": "color", "value": "blue"}, "파랑"),
    word("yellow", "Yellow", ["yellow"], 2, "color", "core", {"type": "attr", "slot": "color", "value": "yellow"}, "노랑"),
    word("green", "Green", ["green"], 2, "color", "core", {"type": "attr", "slot": "color", "value": "green"}, "초록"),
    word("big", "Big", ["big"], 2, "size", "core", {"type": "attr", "slot": "size", "value": "big"}, "큰"),
    word("small", "Small", ["small", "little"], 2, "size", "core", {"type": "attr", "slot": "size", "value": "small"}, "작은"),
    word("one", "One", ["one", "1"], 2, "number", "core", {"type": "attr", "slot": "count", "value": 1}, "하나"),
    word("two", "Two", ["two", "2"], 2, "number", "core", {"type": "attr", "slot": "count", "value": 2}, "둘"),
    word("three", "Three", ["three", "3"], 2, "number", "core", {"type": "attr", "slot": "count", "value": 3}, "셋"),
    word("four", "Four", ["four", "4"], 2, "number", "core", {"type": "attr", "slot": "count", "value": 4}, "넷"),
    word("five", "Five", ["five", "5"], 2, "number", "core", {"type": "attr", "slot": "count", "value": 5}, "다섯"),
    word("egg", "Egg", ["egg", "eggs"], 2, "object", "core", {"type": "sprite", "kind": "egg"}, "알"),
    word("ball", "Ball", ["ball", "balls"], 2, "object", "core", {"type": "sprite", "kind": "ball"}, "공"),
    word("tree", "Tree", ["tree", "trees"], 2, "object", "core", {"type": "sprite", "kind": "tree"}, "나무"),
    word("socks", "Socks", ["socks", "sock"], 2, "object", "core", {"type": "sprite", "kind": "socks"}, "양말"),
    word("apple", "Apple", ["apple", "apples"], 2, "object", "core", {"type": "sprite", "kind": "apple"}, "사과"),
]
BY_ID = {w["id"]: w for w in TABLE}


def test_the_table_is_what_the_brief_says():
    assert len(TABLE) == 45 and len(BY_ID) == 45
    assert sum(1 for w in TABLE if w["level"] == 1) == 27


# 진짜 단어장은 표의 단어를 모두 갖되, 그보다 많아도 된다: 단어를 더하는 일은 JSON 한 줄을 더하는 일이고(spec G4),
# 그 한 줄 때문에 테스트를 고칠 일은 없어야 한다. 그래서 아래는 "표 ⊆ 단어장", 개수는 ">=" 로 본다.


def test_bank_has_every_table_word(bank):
    assert set(BY_ID) <= {w.id for w in bank.words}
    assert len(bank.words) >= len(BY_ID)


@pytest.mark.parametrize("expected", TABLE, ids=lambda w: w["id"])
def test_every_word_matches_the_table(bank, expected):
    got = next(w for w in bank.words if w.id == expected["id"])
    assert got.en == expected["en"]
    assert got.accept == tuple(expected["accept"])
    assert got.level == expected["level"] and got.category == expected["category"]
    assert got.source == expected["source"]
    assert got.present == expected["present"]
    assert got.ko == expected["ko"]


def test_word_counts_per_level(bank):
    assert len(bank.up_to(1)) >= 27
    assert len(bank.up_to(2)) >= 45
    assert len([w for w in bank.words if w.level == 2]) >= 18


def test_accept_zero_is_the_normalized_en(bank):
    for w in bank.words:
        assert w.accept[0] == normalize(w.en), w.id
    # {object} 자리 표시도 normalize 를 지나도 남는다(결정 R2)
    by_id = {w.id: w for w in bank.words}
    assert by_id["throw-out"].accept[0] == "throw out the {object}" == normalize("Throw out the {object}")
    assert by_id["chomp"].accept[0] == "chomp the {object}"


def test_every_accept_is_already_normalized_and_english(bank):
    for w in bank.words:
        assert len(set(w.accept)) == len(w.accept), w.id
        for phrase in w.accept:
            assert phrase and phrase == normalize(phrase) and not has_hangul(phrase), (w.id, phrase)


def test_no_accept_phrase_belongs_to_two_words(bank):
    owner = {}
    for w in bank.words:
        for phrase in w.accept:
            assert phrase not in owner, (phrase, owner.get(phrase), w.id)
            owner[phrase] = w.id


def test_ids_are_unique_and_kebab_case(bank):
    ids = [w.id for w in bank.words]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in ids)


def test_korean_meanings_are_short_and_hangul_only(bank):
    # 힌트에 "뜻 → English" 로 나온다: 6~7살이 읽는 짧은 우리말, 영어 글자는 없다.
    for w in bank.words:
        assert has_hangul(w.ko) and len(w.ko) <= 14 and not re.search(r"[A-Za-z0-9]", w.ko), w.id
        assert not has_hangul(w.en), w.id


def test_sources_and_levels_are_in_range(bank):
    assert {w.source for w in bank.words} <= set(SOURCES) == {"shunibini-A", "shunibini-B", "core"}
    assert all(1 <= w.level <= MAX_LEVEL for w in bank.words)
    # 슈니비니 카드 문장(표에 16개)은 A 또는 B, 멍멍이 명령·인사·색·숫자·물건은 core
    assert sum(1 for w in bank.words if w.source.startswith("shunibini")) >= 16


def test_chomp_only_ever_bites_food(bank, catalog):
    for w in bank.words:
        p = w.present
        if p["type"] == "act" and p["actor"] == "chomp" and p["action"] == "chomp":
            assert catalog.is_food(p["needs"]), w.id


def test_word_files_are_utf8_with_one_word_per_line():
    files = sorted(WORDS_DIR.glob("*.json"))
    assert {f.name for f in files} >= {"level1_actions.json", "level1_social.json", "level2_starter.json"}
    for f in files:
        text = f.read_bytes().decode("utf-8-sig")  # 메모장이 붙인 BOM 은 괜찮다(로더도 utf-8-sig 로 읽는다)
        entries = json.loads(text)
        assert isinstance(entries, list) and entries
        # 한 줄에 한 단어: 단어를 더하는 것이 JSON 한 줄을 더하는 일이다
        body = [ln for ln in text.splitlines() if ln.strip() not in ("", "[", "]")]
        assert len(body) == len(entries)
        assert all(ln.strip().startswith("{") for ln in body)


# ---------------------------------------------------------------- WordBank 조회

def test_up_to_filters_by_level_and_keeps_file_order(bank):
    assert bank.up_to(0) == []
    one = bank.up_to(1)
    assert one and all(w.level == 1 for w in one)
    assert bank.up_to(2) == [w for w in bank.words if w.level <= 2]
    assert bank.up_to(MAX_LEVEL) == list(bank.words) == bank.up_to(99)
    assert [w.id for w in one] == [w.id for w in bank.words if w.level == 1]
    assert isinstance(one, list)


def test_up_to_returns_a_fresh_list(bank):
    before = len(bank.up_to(1))
    first = bank.up_to(1)
    first.clear()
    assert before and len(bank.up_to(1)) == before


def test_attrs_color_size_count(bank):
    assert bank.attrs("color").items() >= {"red": "red", "blue": "blue", "yellow": "yellow", "green": "green"}.items()
    assert bank.attrs("size").items() >= {"big": "big", "small": "small", "little": "small"}.items()
    count = bank.attrs("count")
    assert count.items() >= {"one": 1, "1": 1, "two": 2, "2": 2, "three": 3, "3": 3, "four": 4, "4": 4,
                             "five": 5, "5": 5}.items()
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in count.values())


def test_attrs_unknown_slot_is_an_error(bank):
    with pytest.raises(ValueError, match="shape"):
        bank.attrs("shape")


def test_attrs_and_objects_return_copies(bank):
    bank.attrs("color")["red"] = "blue"
    bank.attrs("color").clear()
    bank.objects().clear()
    assert bank.attrs("color")["red"] == "red" and bank.objects()["ball"] == "ball"


def test_objects_map_singular_and_plural_to_the_kind(bank, catalog):
    assert bank.objects().items() >= {
        "egg": "egg", "eggs": "egg", "ball": "ball", "balls": "ball", "tree": "tree", "trees": "tree",
        "socks": "socks", "sock": "socks", "apple": "apple", "apples": "apple",
    }.items()
    assert set(bank.objects().values()) <= set(catalog.objects)


def test_every_attr_value_exists_in_the_catalog(bank, catalog):
    assert set(bank.attrs("color").values()) <= set(catalog.colors)
    assert set(bank.attrs("size").values()) <= set(catalog.sizes)
    assert set(bank.attrs("count").values()) <= set(range(1, catalog.max_count + 1))


def test_word_is_frozen_and_hashable(bank):
    w = bank.words[0]
    assert isinstance(w, Word) and isinstance(w.accept, tuple) and isinstance(bank.words, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.en = "Other"
    assert hash(w) == hash(dataclasses.replace(w))
    assert len({*bank.words}) == len(bank.words)
    with pytest.raises(AttributeError):
        bank.words = ()


def test_bank_can_be_built_from_words_directly(bank):
    moves = [w for w in bank.words if w.present["type"] in ("act", "mood")]  # 꾸밈말도 물건도 아닌 단어만
    sub = WordBank(moves)
    assert len(sub.words) == len(moves) >= 27 and sub.attrs("color") == {} and sub.objects() == {}
    # 손으로 만든 단어의 accept 도 normalize 해서 넣는다
    hand = Word(id="x", en="Red", ko="빨강", accept=("Red!",), level=2, category="color", source="core",
                present={"type": "attr", "slot": "color", "value": "red"})
    assert WordBank([hand]).attrs("color") == {"red": "red"}


# ---------------------------------------------------------------- 깨진 단어장

def good(**over):
    entry = {"id": "x", "en": "Jump", "ko": "뛰어", "accept": ["jump"], "level": 1, "category": "action",
             "source": "core", "present": {"type": "act", "actor": "chomp", "action": "jump"}}
    entry.update(over)
    return entry


def write_words(tmp_path, entries, name="w.json"):
    (tmp_path / name).write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def load_entries(tmp_path, entries, catalog):
    return load_words(write_words(tmp_path, entries), catalog)


def test_the_good_entry_used_by_these_tests_loads(tmp_path, catalog):
    bank = load_entries(tmp_path, [good()], catalog)
    assert [w.id for w in bank.words] == ["x"]


@pytest.mark.parametrize("over, match", [
    # 카탈로그에 없는 이름
    (dict(present=act("cat", "jump")), r"cat"),
    (dict(present=act("chomp", "fly")), r"fly"),
    (dict(present=act("chomp", "turn_around")), r"turn_around"),     # dino 의 동작이다
    (dict(present=act("dino", "chomp")), r"chomp"),                   # chomp 의 동작이다
    (dict(present=act("dino", "kick", needs="dragon")), r"needs.*dragon"),
    (dict(present=act("dino", "clean_up", removes="dragon")), r"removes.*dragon"),
    (dict(present={"type": "sprite", "kind": "dragon"}), r"dragon"),
    (dict(present={"type": "mood", "actor": "cat", "face": "happy"}), r"cat"),
    (dict(present={"type": "mood", "actor": "all", "face": "angry"}), r"angry"),
    (dict(present={"type": "mood", "actor": "chomp", "face": "angry"}), r"angry"),
    (dict(present={"type": "attr", "slot": "color", "value": "magenta"}), r"magenta"),
    (dict(present={"type": "attr", "slot": "size", "value": "huge"}), r"huge"),
    (dict(present={"type": "attr", "slot": "shape", "value": "round"}), r"shape"),
    (dict(present={"type": "attr", "slot": "count", "value": 0}), r"count"),
    (dict(present={"type": "attr", "slot": "count", "value": 6}), r"count"),      # max_count 는 5
    (dict(present={"type": "attr", "slot": "count", "value": "3"}), r"count"),
    (dict(present={"type": "attr", "slot": "count", "value": True}), r"count"),
    (dict(present={"type": "attr", "slot": "count", "value": 2.5}), r"count"),
    (dict(present={"type": "attr", "slot": "color", "value": ["red"]}), r"color"),
    # 알 수 없는 present 모양
    (dict(present={"type": "quiz"}), r"quiz"),
    (dict(present={"type": ["act"]}), r"\btype\b"),
    (dict(present={"actor": "chomp", "action": "jump"}), r"\btype\b"),
    (dict(present="act"), r"\bpresent\b"),
    (dict(present=None), r"\bpresent\b"),
    (dict(present=act("chomp", "jump", color="red")), r"color"),                  # 모르는 키
    (dict(present={"type": "act", "actor": "chomp"}), r"action"),                 # 빠진 키
    (dict(present={"type": "sprite"}), r"\bkind\b"),
    (dict(present={"type": "sprite", "kind": ["egg"]}), r"\bkind\b"),
    (dict(present=act("chomp", ["jump"])), r"action"),
    (dict(present=act(["chomp"], "jump")), r"actor"),
    (dict(present=act("dino", "kick", needs=5)), r"needs"),
    (dict(present=act("dino", "kick", needs="{object}"), accept=["jump"]), r"\{object\}"),   # 자리 표시가 있을 곳이 없다
    (dict(en="Clean up", present=act("dino", "clean_up", removes="{object}"), accept=["clean up"]), r"\{object\}"),
    # 멍멍이는 음식만 문다
    (dict(present=act("chomp", "chomp", needs="egg")), r"food"),
    # 자리 표시는 act 의 accept 에만
    (dict(present={"type": "sprite", "kind": "egg"}, accept=["egg", "{object}"], en="Egg", category="object"), r"\{object\}"),
    # 최상위 키
    (dict(id=None), r"\bid\b"),
    (dict(id=""), r"\bid\b"),
    (dict(id="Kick The Ball"), r"\bid\b"),
    (dict(id="kick_the_ball"), r"\bid\b"),
    (dict(id=7), r"\bid\b"),
    (dict(en=""), r"\ben\b"),
    (dict(en=5), r"\ben\b"),
    (dict(en="점프", accept=["점프"]), r"\ben\b"),
    (dict(en="Jump\nnow", accept=["jump now"]), r"\ben\b"),
    (dict(ko="jump"), r"\bko\b"),
    (dict(ko=""), r"\bko\b"),
    (dict(ko=None), r"\bko\b"),
    (dict(ko="뛰어\n"), r"\bko\b"),
    (dict(accept=[]), r"accept"),
    (dict(accept="jump"), r"accept"),
    (dict(accept=["jump", ""]), r"accept"),
    (dict(accept=["jump", 3]), r"accept"),
    (dict(accept=["jump", "Jump"]), r"accept.*Jump"),             # 정규화 안 된 모양
    (dict(accept=["jump", "jump!"]), r"accept.*jump!"),
    (dict(accept=["jump", "let's"]), r"accept"),
    (dict(accept=["jump", "{foo}"]), r"accept"),
    (dict(accept=["jump", "점프"]), r"accept"),
    (dict(accept=["jump", "jump"]), r"accept.*duplicate|duplicate.*accept"),
    (dict(accept=["hop", "jump"]), r"accept\[0\]"),               # 첫 칸은 normalize(en)
    (dict(level=0), r"\blevel\b"),
    (dict(level=MAX_LEVEL + 1), r"\blevel\b"),
    (dict(level="1"), r"\blevel\b"),
    (dict(level=True), r"\blevel\b"),
    (dict(level=1.0), r"\blevel\b"),
    (dict(category=""), r"\bcategory\b"),
    (dict(category="Action"), r"\bcategory\b"),
    (dict(category=3), r"\bcategory\b"),
    (dict(source="shunibini-C"), r"\bsource\b"),
    (dict(source="shunibini-a"), r"\bsource\b"),
    (dict(source=""), r"\bsource\b"),
    (dict(source=None), r"\bsource\b"),
])
def test_a_broken_word_is_a_value_error_naming_the_problem(tmp_path, catalog, over, match):
    with pytest.raises(ValueError, match=match):
        load_entries(tmp_path, [good(**over)], catalog)


def test_error_names_the_file_and_the_word(tmp_path, catalog):
    write_words(tmp_path, [good(), good(id="y", accept=["hop"], en="Hop", present=act("chomp", "fly"))], name="level9_odd.json")
    with pytest.raises(ValueError) as err:
        load_words(tmp_path, catalog)
    message = str(err.value)
    assert "level9_odd.json" in message and "'y'" in message and "fly" in message


@pytest.mark.parametrize("key", ["id", "en", "ko", "accept", "level", "category", "source", "present"])
def test_missing_key_names_the_key(tmp_path, catalog, key):
    entry = good()
    del entry[key]
    with pytest.raises(ValueError, match=rf"missing.*{key}"):
        load_entries(tmp_path, [entry], catalog)


def test_unknown_key_is_rejected(tmp_path, catalog):
    # "sorce" 같은 오타가 조용히 무시되면 그 단어는 source 없이 들어간다
    with pytest.raises(ValueError, match="sorce"):
        load_entries(tmp_path, [good(sorce="core")], catalog)


def test_duplicate_id_in_one_file(tmp_path, catalog):
    with pytest.raises(ValueError, match=r"duplicate.*'x'|'x'.*duplicate"):
        load_entries(tmp_path, [good(), good(accept=["jump"], en="Jump")], catalog)


def test_duplicate_id_across_files_names_both_files(tmp_path, catalog):
    write_words(tmp_path, [good()], name="a.json")
    write_words(tmp_path, [good(accept=["hop"], en="Hop")], name="b.json")
    with pytest.raises(ValueError) as err:
        load_words(tmp_path, catalog)
    assert "duplicate" in str(err.value) and "a.json" in str(err.value) and "b.json" in str(err.value)


def test_same_accept_phrase_in_two_words_is_rejected(tmp_path, catalog):
    other = good(id="y", en="Jump", accept=["jump"])
    with pytest.raises(ValueError, match=r"'jump'.*'x'|'x'.*'jump'"):
        load_entries(tmp_path, [good(), other], catalog)


def test_a_mood_for_all_must_fit_every_actor(tmp_path, catalog):
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    data = copy.deepcopy(raw)
    data["actors"]["chomp"]["faces"] = ["happy", "sad"]          # chomp 에게 'shy' 가 없다
    alt = tmp_path / "catalog.json"
    alt.write_text(json.dumps(data), encoding="utf-8")
    words = tmp_path / "words"
    words.mkdir()
    shy = good(id="shy", en="Shy", accept=["shy"], category="emotion", present={"type": "mood", "actor": "all", "face": "shy"})
    write_words(words, [shy])
    with pytest.raises(ValueError, match="shy"):
        load_words(words, load_catalog(alt))
    # dino 만 부끄러워하는 건 괜찮다
    write_words(words, [{**shy, "present": {"type": "mood", "actor": "dino", "face": "shy"}}])
    assert load_words(words, load_catalog(alt)).words[0].present["actor"] == "dino"


def test_object_placeholder_is_allowed_in_act_accepts_and_in_needs(tmp_path, catalog):
    entry = good(id="k", en="Kick the ball", accept=["kick the ball", "kick the {object}"], category="action",
                 present=act("dino", "kick", needs="ball"))
    thrown = good(id="t", en="Throw out the {object}", accept=["throw out the {object}"],
                  present=act("dino", "throw_out", removes="{object}"))
    bank = load_entries(tmp_path, [entry, thrown], catalog)
    assert bank.words[1].present["removes"] == "{object}"


def test_chomp_eating_food_is_fine(tmp_path, catalog):
    entry = good(id="c", en="Eat", accept=["eat"], present=act("chomp", "chomp", needs="cookie"))
    assert load_entries(tmp_path, [entry], catalog).words[0].present["needs"] == "cookie"


# ---------------------------------------------------------------- 깨진 파일과 폴더

def test_missing_directory(tmp_path, catalog):
    with pytest.raises(ValueError, match="not found"):
        load_words(tmp_path / "nope", catalog)


def test_directory_without_word_files(tmp_path, catalog):
    (tmp_path / "README.md").write_text("no words here", encoding="utf-8")
    with pytest.raises(ValueError, match=r"no .*json"):
        load_words(tmp_path, catalog)


def test_other_files_are_ignored(tmp_path, catalog):
    write_words(tmp_path, [good()])
    (tmp_path / "notes.txt").write_text("not json at all", encoding="utf-8")
    (tmp_path / "old.json.bak").write_text("{ nope", encoding="utf-8")
    assert len(load_words(tmp_path, catalog).words) == 1


def test_files_load_in_name_order(tmp_path, catalog):
    write_words(tmp_path, [good(id="b", en="Bark", accept=["bark"], present=act("chomp", "bark"))], name="b.json")
    write_words(tmp_path, [good(id="a")], name="a.json")
    assert [w.id for w in load_words(tmp_path, catalog).words] == ["a", "b"]


def test_invalid_json_is_a_value_error_naming_the_file(tmp_path, catalog):
    (tmp_path / "bad.json").write_text("[ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        load_words(tmp_path, catalog)


def test_a_utf8_bom_from_notepad_loads(tmp_path, catalog):
    # 부모는 Windows 메모장으로 단어장을 고친다. "UTF-8(BOM)" 으로 저장해도 읽는다(단어장은 서버만 읽는다. 브라우저는 안 읽는다)
    (tmp_path / "w.json").write_bytes(b"\xef\xbb\xbf" + json.dumps([good()], ensure_ascii=False).encode("utf-8"))
    bank = load_words(tmp_path, catalog)
    assert [w.id for w in bank.words] == ["x"] and bank.words[0].ko == "뛰어"


def test_non_utf8_bytes_are_a_value_error(tmp_path, catalog):
    (tmp_path / "w.json").write_bytes(b'[{"id": "\xff"}]')
    with pytest.raises(ValueError, match="w.json"):
        load_words(tmp_path, catalog)


def test_top_level_must_be_a_list_of_objects(tmp_path, catalog):
    (tmp_path / "w.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"w.json.*array"):
        load_words(tmp_path, catalog)
    (tmp_path / "w.json").write_text(json.dumps([good(), "jump"]), encoding="utf-8")
    with pytest.raises(ValueError, match=r"w.json.*object"):
        load_words(tmp_path, catalog)


def test_an_empty_list_is_fine_as_long_as_another_file_has_words(tmp_path, catalog):
    write_words(tmp_path, [], name="empty.json")
    write_words(tmp_path, [good()], name="w.json")
    assert len(load_words(tmp_path, catalog).words) == 1


def test_duplicate_json_key_is_rejected(tmp_path, catalog):
    # 같은 키가 두 번 나오면 json 은 뒤엣것만 남기고 조용히 넘어간다
    text = json.dumps([good()]).replace('"level": 1', '"level": 1, "level": 2')
    assert text.count('"level"') == 2
    (tmp_path / "w.json").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"w.json.*level"):
        load_words(tmp_path, catalog)
