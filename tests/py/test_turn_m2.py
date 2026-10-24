"""M2 턴 처리기(태스크 13): fastpath, 알 부화(hatch), 단어장·카탈로그가 든 시스템 프롬프트.

진짜 Claude 는 부르지 않는다. FakeLLM 이 정해 둔 답을 돌려준다(쿼터 사용 0).
흐름 계약(브리프 7단계):
  1. 빈 입력/군소리 → 템플릿          2. 세션의 첫 영어 → 1줄 ops 맨 앞에 hatch
  3. fastpath exact → 1줄 fast, 2줄 template(LLM 없음)   4. partial → 1줄 fast, 2줄 LLM(applied_ops)
  5. 매치 없음 → 1줄 ack(부화 턴이면 hatch 만 든 fast), 2줄 LLM   6. 한글 입력 → fastpath·부화 없음, ops 없음
  7. 2줄 ops 는 1줄 ops 를 되풀이하지 않는다
"""
import json
import re
from pathlib import Path

import jsonschema
import pytest

import server.turn as turn_module
from server.catalog import load_catalog
from server.fastpath import match
from server.llm import LLMError
from server.llm.fake import FakeLLM
from server.prompt_builder import catalog_summary, vocab_lines
from server.reply import validate_reply
from server.session_log import SessionLog
from server.text import has_hangul
from server.turn import TurnOrchestrator
from server.words import load_words

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "prompts" / "chomp_system.md").read_text(encoding="utf-8")
CATALOG = load_catalog(ROOT / "shared" / "catalog.json")
BANK = load_words(ROOT / "content" / "words", CATALOG)
SCHEMA = json.loads((ROOT / "shared" / "schemas" / "turn_reply.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
LINE_KEYS = ("kind", "turn_id", "source", "latency")

HATCH = {"op": "act", "actor": "dino", "action": "hatch"}
# Task 14 initialScene 을 toWire 로 보낸 모양: 둥지와 그 속의 알, 두 캐릭터(R4)
START_SCENE = [
    {"id": "nest1", "kind": "nest", "place": {"zone": "left"}},
    {"id": "egg1", "kind": "egg", "place": {"rel": "in", "ref": "nest1"}},
    {"id": "chomp", "kind": "chomp", "state": "idle"},
    {"id": "dino", "kind": "dino", "state": "idle"},
]
HATCHED_SCENE = [item for item in START_SCENE if item["id"] != "egg1"]


def reply_json(say="Excellent!", ops=(), lang="en", **extra):
    obj = {"say": say, "recast": None, "choice": None, "korean_hint": None, "ops": list(ops), "mood": None,
           "utterance": {"lang": lang, "words_used": [], "errors": []}}
    obj.update(extra)
    return json.dumps(obj, ensure_ascii=False)


# 한글 입력에서도 ops 를 내는 답: 서버가 ops 를 비우는지 본다
DEFAULT_REPLY = reply_json(say="Red egg? Say: red egg!", ops=[{"op": "add", "kind": "egg", "color": "red"}])


def req(session_id, turn_id, alts, scene=None, **extra):
    body = {
        "session_id": session_id,
        "turn_id": turn_id,
        "asr": {"engine": "webspeech", "lang": "en-US",
                "alternatives": [{"t": t, "c": round(0.9 - i * 0.1, 2)} for i, t in enumerate(alts)]},
        "hint_requested": False,
        "scene": START_SCENE if scene is None else scene,
    }
    body.update(extra)
    return body


def make_m2(tmp_path, script=None, log=None):
    llm = FakeLLM(script or [DEFAULT_REPLY])
    orch = TurnOrchestrator(llm, log or SessionLog(tmp_path / "sessions"), TEMPLATE, bank=BANK, catalog=CATALOG)
    return orch, llm


def log_events(tmp_path, session_id):
    path = tmp_path / "sessions" / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def turn(orch, session_id, turn_id, alts, scene=None):
    return list(orch.handle(req(session_id, turn_id, alts, scene)))


def hatch_first(orch, session_id="s"):
    """세션을 열고 첫 영어 한마디로 알을 깨운다. 그다음 턴부터는 부화 턴이 아니다."""
    orch.start_session(session_id)
    first, _ = turn(orch, session_id, 1, ["jump"])
    assert first["ops"][0] == HATCH


def schema_ok(reply_line):
    body = {k: v for k, v in reply_line.items() if k not in LINE_KEYS}
    VALIDATOR.validate(body)
    return body


@pytest.fixture
def llm():
    return FakeLLM([DEFAULT_REPLY])


@pytest.fixture
def orch_m2(llm, tmp_path):
    return TurnOrchestrator(llm, SessionLog(tmp_path / "sessions"), TEMPLATE, bank=BANK, catalog=CATALOG)


# ---------------------------------------------------------------- brief Step 1


def test_exact_fastpath_skips_llm(orch_m2, llm):
    orch_m2.start_session("s"); lines = list(orch_m2.handle(req("s", 1, ["big red egg"])))
    assert lines[0]["kind"] == "fast" and lines[1]["source"] == "template" and llm.calls == []
    assert {"op": "act", "actor": "dino", "action": "hatch"} == lines[0]["ops"][0]


def test_hatch_only_once(orch_m2, llm):
    orch_m2.start_session("s"); list(orch_m2.handle(req("s", 1, ["jump"])))
    second = list(orch_m2.handle(req("s", 2, ["jump"])))[0]
    assert all(op.get("action") != "hatch" for op in second["ops"])


def test_partial_goes_to_llm_with_applied_ops(orch_m2, llm):
    orch_m2.start_session("s"); list(orch_m2.handle(req("s", 1, ["jump"])))
    lines = list(orch_m2.handle(req("s", 2, ["kick ball"])))
    assert lines[0]["kind"] == "fast" and lines[1]["source"] == "llm"
    assert '"applied_ops"' in llm.calls[-1] and '"kick"' in llm.calls[-1]


def test_korean_never_hatches_or_moves(orch_m2, llm):
    orch_m2.start_session("s"); lines = list(orch_m2.handle(req("s", 1, ["빨간 알"])))
    assert lines[0]["kind"] == "ack" and lines[1]["ops"] == []


def test_system_prompt_contains_vocab(orch_m2, llm):
    orch_m2.start_session("s")
    assert "Kick the ball | 공을 차" in llm.system_prompt and "{VOCAB}" not in llm.system_prompt


# ---------------------------------------------------------------- 3. exact → 템플릿


def test_exact_reply_is_a_full_template(tmp_path):
    orch, llm = make_m2(tmp_path)
    hatch_first(orch)
    first, reply = turn(orch, "s", 2, ["kick the ball"], HATCHED_SCENE)
    expected = match(["kick the ball"], BANK, CATALOG, HATCHED_SCENE, turn_id=2)
    assert first == {"kind": "fast", "turn_id": 2, "ops": expected.ops, "sfx": "pop"}
    assert [op["op"] for op in first["ops"]] == ["add", "act"]
    assert (reply["kind"], reply["turn_id"], reply["source"]) == ("reply", 2, "template")
    assert reply["say"] == expected.say and reply["korean_hint"] == "공을 차 → Kick the ball!"
    assert reply["ops"] == []  # 7. 1줄 ops 를 되풀이하지 않는다
    assert reply["utterance"] == {"lang": "en", "words_used": ["kick-the-ball"], "errors": []}
    assert reply["latency"]["llm_ms"] is None and llm.calls == []
    schema_ok(reply)


@pytest.mark.parametrize("said, scene, hint", [
    # 동작/표정 단어: "{ko} → {En}!" ({object} 는 들어간 물건 말, 없으면 단어의 기본 물건)
    (["turn around"], HATCHED_SCENE, "뒤로 돌아 → Turn around!"),
    (["chomp the apple"], HATCHED_SCENE, "덥석 먹어 → Chomp the apple!"),
    (["eat the apple"], HATCHED_SCENE, "덥석 먹어 → Chomp the apple!"),
    (["throw out the egg"], [*HATCHED_SCENE, {"id": "egg2", "kind": "egg"}], "버려 → Throw out the egg!"),
    (["kick the egg"], HATCHED_SCENE, "공을 차 → Kick the ball!"),  # En 에 자리가 없으면 En 그대로(뜻과 짝)
    (["good job"], HATCHED_SCENE, "잘했어 → Good job!"),
    (["lets clap"], HATCHED_SCENE, "박수 치자 → Let's clap!"),
    # 물건 문법: 맞은 단어들의 ko + " → " + 영어 구절
    (["big red egg"], HATCHED_SCENE, "큰 빨강 알 → A big red egg!"),
    (["two balls"], HATCHED_SCENE, "둘 공 → Two balls!"),
    (["socks"], HATCHED_SCENE, "양말 → Socks!"),
])
def test_template_korean_hint(tmp_path, said, scene, hint):
    orch, _ = make_m2(tmp_path)
    hatch_first(orch)
    _, reply = turn(orch, "s", 2, said, scene)
    assert reply["source"] == "template" and reply["korean_hint"] == hint
    assert not has_hangul(reply["say"]) and "{object}" not in reply["korean_hint"]
    schema_ok(reply)


# ---------------------------------------------------------------- 2. 부화


def test_first_english_turn_without_a_match_sends_hatch_alone(tmp_path):
    orch, llm = make_m2(tmp_path, [reply_json(say="Baby Dino is here! Hello!")])
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, ["i like dinosaurs"])
    assert first == {"kind": "fast", "turn_id": 1, "ops": [HATCH], "sfx": "pop"}
    assert reply["source"] == "llm" and reply["ops"] == []
    sent = json.loads(llm.calls[0])
    assert sent["applied_ops"] == [HATCH]
    assert "egg1" not in [item.get("id") for item in sent["scene"]]  # hatch 가 알을 없앤 장면을 보여 준다


def test_no_match_after_the_hatch_is_a_plain_ack(tmp_path):
    orch, llm = make_m2(tmp_path, [reply_json(say="Me too!")])
    hatch_first(orch)
    first, reply = turn(orch, "s", 2, ["i like dinosaurs"], HATCHED_SCENE)
    assert first == {"kind": "ack", "turn_id": 2}
    assert reply["source"] == "llm" and json.loads(llm.calls[-1])["applied_ops"] == []


@pytest.mark.parametrize("quiet", [[], ["uh"], ["um", "hmm"]])
def test_silence_does_not_hatch(tmp_path, quiet):
    orch, llm = make_m2(tmp_path)
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, quiet)
    assert first == {"kind": "ack", "turn_id": 1}
    assert reply["source"] == "template" and llm.calls == []
    assert turn(orch, "s", 2, ["jump"])[0]["ops"][0] == HATCH  # 처음 들린 영어에서 부화한다


def test_korean_first_then_english_hatches_on_the_english_turn(tmp_path):
    orch, _ = make_m2(tmp_path)
    orch.start_session("s")
    assert turn(orch, "s", 1, ["빨간 알"])[0] == {"kind": "ack", "turn_id": 1}
    assert turn(orch, "s", 2, ["red egg"])[0]["ops"][0] == HATCH


def test_hatch_turn_never_targets_egg1(tmp_path):
    orch, _ = make_m2(tmp_path)
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, ["kick the egg"])
    assert first["ops"][0] == HATCH
    rest = first["ops"][1:]
    assert [op["op"] for op in rest] == ["add", "act"] and rest[0]["kind"] == "egg"
    assert rest[1]["target"] == rest[0]["id"]
    assert "egg1" not in json.dumps(first["ops"]) and reply["source"] == "template"


def test_hatch_turn_does_not_reuse_the_hatched_egg_id(tmp_path):
    orch, _ = make_m2(tmp_path)
    orch.start_session("s")
    first, _ = turn(orch, "s", 1, ["big red egg"])
    assert first["ops"] == [HATCH, {"op": "add", "kind": "egg", "id": "egg2", "count": 1,
                                    "size": "big", "color": "red"}]


def test_hatch_turn_llm_ops_cannot_touch_egg1(tmp_path):
    script = [reply_json(say="Kick! Good job!", ops=[
        {"op": "act", "actor": "dino", "action": "kick", "target": "egg1"},
        {"op": "remove", "target": "egg1"},
        {"op": "add", "kind": "egg", "id": "egg1"},
    ])]
    orch, _ = make_m2(tmp_path, script)
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, ["dino please kick it"])
    assert first["ops"] == [HATCH]
    assert reply["ops"] == [{"op": "add", "kind": "egg", "id": "egg2", "count": 1}]


def test_a_new_session_hatches_again(tmp_path):
    orch, _ = make_m2(tmp_path)
    orch.start_session("a")
    assert turn(orch, "a", 1, ["jump"])[0]["ops"][0] == HATCH
    orch.start_session("b")
    assert turn(orch, "b", 1, ["jump"])[0]["ops"][0] == HATCH
    assert turn(orch, "c", 1, ["jump"])[0]["ops"][0] == HATCH  # 모르는 session_id: 저절로 새 세션
    assert turn(orch, "c", 2, ["jump"])[0]["ops"] == [{"op": "act", "actor": "chomp", "action": "jump"}]


# ---------------------------------------------------------------- 서버를 다시 켠 뒤: 화면은 이미 부화했다

# 열려 있던 아이 화면(toWire). 부화로 둥지 알 egg1 이 없어진 뒤 "red egg" 가 새 알에 egg1 을 다시 주었다(next_ids).
RESTARTED_SCENE = [
    {"id": "chomp", "kind": "chomp", "state": "idle", "face": "happy"},
    {"id": "dino", "kind": "dino", "state": "idle", "face": "happy", "hatched": True},
    {"id": "nest1", "kind": "nest", "place": {"zone": "left"}},
    {"id": "egg1", "kind": "egg", "color": "red"},
]


@pytest.mark.parametrize("said, ops", [
    (["kick the egg"], [{"op": "act", "actor": "dino", "action": "kick", "target": "egg1"}]),
    (["throw out the egg"], [{"op": "act", "actor": "dino", "action": "throw_out"}, {"op": "remove", "target": "egg1"}]),
])
def test_restart_with_a_hatched_scene_keeps_egg1_targetable_by_the_fastpath(tmp_path, said, ops):
    orch, llm = make_m2(tmp_path)  # 새로 켠 서버: 이 세션을 모른다(implicit start)
    first, reply = turn(orch, "s", 5, said, RESTARTED_SCENE)
    assert first == {"kind": "fast", "turn_id": 5, "ops": ops, "sfx": "pop"}  # hatch 가 없다
    assert reply["source"] == "template" and llm.calls == []


def test_restart_with_a_hatched_scene_keeps_egg1_targetable_by_the_validator(tmp_path):
    kick = {"op": "act", "actor": "dino", "action": "kick", "target": "egg1"}
    orch, llm = make_m2(tmp_path, [reply_json(say="Kick! Good job!", ops=[kick])])
    first, reply = turn(orch, "s", 5, ["dino please kick it"], RESTARTED_SCENE)
    assert first == {"kind": "ack", "turn_id": 5}
    sent = json.loads(llm.calls[0])
    assert sent["applied_ops"] == [] and "egg1" in [item["id"] for item in sent["scene"]]
    assert reply["source"] == "llm" and reply["ops"] == [kick]


def test_the_pages_hatched_flag_is_remembered_for_the_session(tmp_path):
    orch, _ = make_m2(tmp_path)
    turn(orch, "s", 5, ["빨간 알"], RESTARTED_SCENE)  # 한국어 턴이어도 화면이 알려 준 부화는 기억한다
    first, _ = turn(orch, "s", 6, ["jump"], HATCHED_SCENE)  # 이번 장면에는 hatched 표시가 없다
    assert first["ops"] == [{"op": "act", "actor": "chomp", "action": "jump"}]


def test_a_page_that_says_not_hatched_still_hatches(tmp_path):
    scene = [{**item, "hatched": False} if item["id"] == "dino" else item for item in START_SCENE]
    orch, _ = make_m2(tmp_path)
    assert turn(orch, "s", 1, ["jump"], scene)[0]["ops"][0] == HATCH
    weird = [{**item, "hatched": "true"} if item["id"] == "dino" else item for item in START_SCENE]
    assert turn(orch, "t", 1, ["jump"], weird)[0]["ops"][0] == HATCH  # true(불리언)만 깨어난 것이다


# ---------------------------------------------------------------- 4·7. partial → LLM, 되풀이 없음


def test_partial_line_two_never_repeats_line_one(tmp_path):
    repeat = [
        {"op": "add", "kind": "ball", "count": 1},
        {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"},
        {"op": "set", "target": "chomp", "face": "happy"},
    ]
    orch, llm = make_m2(tmp_path, [reply_json(say="Kick the ball! Your turn!", ops=repeat)])
    hatch_first(orch)
    first, reply = turn(orch, "s", 2, ["kick ball"], HATCHED_SCENE)
    assert first["ops"] == [{"op": "add", "kind": "ball", "id": "ball1", "count": 1},
                            {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}]
    assert reply["source"] == "llm" and reply["ops"] == [{"op": "set", "target": "chomp", "face": "happy"}]
    assert json.loads(llm.calls[-1])["applied_ops"] == first["ops"]
    assert "ops_already_applied" in log_events(tmp_path, "s")[-1]["issues"]


def test_turn_ops_stay_within_four_across_both_lines(tmp_path):
    extra = [
        {"op": "add", "kind": "cookie", "id": "cookie1", "count": 1},
        {"op": "act", "actor": "chomp", "action": "chomp", "target": "cookie1"},
        {"op": "set", "target": "dino", "face": "happy"},
        {"op": "act", "actor": "chomp", "action": "spin"},
    ]
    orch, _ = make_m2(tmp_path, [reply_json(say="Kick the ball! Your turn!", ops=extra)])
    hatch_first(orch)
    first, reply = turn(orch, "s", 2, ["kick ball"], HATCHED_SCENE)
    assert len(first["ops"]) == 2 and reply["ops"] == extra[:2]
    assert "ops_trimmed" in log_events(tmp_path, "s")[-1]["issues"]


def test_partial_validates_against_the_scene_before_line_one(tmp_path):
    # 1줄이 toys1·toys2 를 치웠어도 LLM 이 새로 내놓는 장난감은 그 id 를 다시 쓰지 않는다(치우기 전 장면 + applied_ops)
    scene = [*HATCHED_SCENE, {"id": "toys1", "kind": "toys"}, {"id": "toys2", "kind": "toys"}]
    orch, _ = make_m2(tmp_path, [reply_json(say="All clean! Good job!", ops=[{"op": "add", "kind": "toys"}])])
    hatch_first(orch)
    first, reply = turn(orch, "s", 2, ["clean up"], scene)  # accept[1]: partial
    assert first["ops"] == [{"op": "act", "actor": "dino", "action": "clean_up"},
                            {"op": "remove", "target": "toys2"}, {"op": "remove", "target": "toys1"}]
    assert reply["source"] == "llm" and reply["ops"] == [{"op": "add", "kind": "toys", "id": "toys3", "count": 1}]


def test_llm_never_sends_hatch(tmp_path):
    orch, _ = make_m2(tmp_path, [reply_json(say="Hatch!", ops=[HATCH, {"op": "act", "actor": "chomp", "action": "bark"}])])
    hatch_first(orch)
    _, reply = turn(orch, "s", 2, ["i like eggs"], HATCHED_SCENE)
    assert reply["ops"] == [{"op": "act", "actor": "chomp", "action": "bark"}]
    assert "ops_hatch_dropped" in log_events(tmp_path, "s")[-1]["issues"]


@pytest.mark.parametrize("script", [[LLMError("dead")], ["not json at all"]])
def test_partial_fallback_keeps_the_right_sentence(tmp_path, script):
    orch, _ = make_m2(tmp_path, script)
    hatch_first(orch)
    first, reply = turn(orch, "s", 2, ["kick ball"], HATCHED_SCENE)
    expected = match(["kick ball"], BANK, CATALOG, HATCHED_SCENE, turn_id=2)
    assert first["ops"] == expected.ops  # 장면은 이미 바뀌었다
    assert reply["source"] == "fallback" and reply["say"] == expected.say and reply["ops"] == []
    assert reply["korean_hint"] == "공을 차 → Kick the ball!"
    schema_ok(reply)


def test_no_match_fallback_is_the_canned_line(tmp_path):
    orch, _ = make_m2(tmp_path, [LLMError("dead")])
    hatch_first(orch)
    _, reply = turn(orch, "s", 2, ["i like eggs"], HATCHED_SCENE)
    assert reply["source"] == "fallback" and reply["say"] in (
        "Hmm! Let's try that again!", "Hmm! One more time, please!")


# ---------------------------------------------------------------- 6. 한국어


def test_korean_skips_fastpath_even_when_a_later_guess_matches(tmp_path):
    orch, llm = make_m2(tmp_path)
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, ["빨간 알", "big red egg"])
    assert first == {"kind": "ack", "turn_id": 1}
    assert reply["source"] == "llm" and reply["ops"] == [] and len(llm.calls) == 1
    assert json.loads(llm.calls[0])["applied_ops"] == []
    assert "ops_dropped_hangul" in log_events(tmp_path, "s")[-1]["issues"]


def test_korean_flag_is_read_before_redact(tmp_path, monkeypatch):
    # M3 의 redact 가 비밀 단어를 바꿔 한글이 사라져도 한국어 입력으로 본다
    monkeypatch.setattr(turn_module, "redact", lambda text: "[secret]")
    orch, llm = make_m2(tmp_path)
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, ["빨간 알"])
    assert first == {"kind": "ack", "turn_id": 1} and reply["ops"] == []
    assert "빨간" not in llm.calls[0]
    assert "ops_dropped_hangul" in log_events(tmp_path, "s")[-1]["issues"]


# ---------------------------------------------------------------- 로그


def test_turn_log_records_the_first_line_ops(tmp_path):
    orch, _ = make_m2(tmp_path)
    orch.start_session("s")
    first, _ = turn(orch, "s", 1, ["big red egg"])
    event = log_events(tmp_path, "s")[-1]
    assert event["type"] == "turn" and event["source"] == "template"
    assert event["fast_ops"] == first["ops"] and event["fastpath"] == "exact" and event["ops"] == []


def test_any_log_failure_still_sends_the_reply(tmp_path):
    class BrokenLog(SessionLog):
        def append(self, session_id, event):
            raise RuntimeError("log bug")

    orch, _ = make_m2(tmp_path, log=BrokenLog(tmp_path / "sessions"))
    orch.start_session("s")
    lines = turn(orch, "s", 1, ["jump"])
    assert [line["kind"] for line in lines] == ["fast", "reply"] and lines[1]["say"]


# ---------------------------------------------------------------- M1 모드는 그대로


def test_without_bank_and_catalog_there_is_no_hatch_or_fastpath(tmp_path):
    llm = FakeLLM([reply_json(say="Hi!")])
    orch = TurnOrchestrator(llm, SessionLog(tmp_path / "sessions"), TEMPLATE)
    orch.start_session("s")
    first, reply = turn(orch, "s", 1, ["jump"])
    assert first == {"kind": "ack", "turn_id": 1} and reply["source"] == "llm" and len(llm.calls) == 1
    assert "(none yet)" in llm.system_prompt


# ---------------------------------------------------------------- 단어장·카탈로그 프롬프트


def test_vocab_lines_one_line_per_word_with_no_placeholder():
    text = vocab_lines(BANK, 2)
    lines = text.splitlines()
    assert len(lines) == len(BANK.up_to(2))
    assert all(line.count(" | ") >= 1 and not has_hangul(line.split(" | ")[0]) for line in lines)
    assert "{object}" not in text
    for line in ("Kick the ball | 공을 차", "Chomp the apple | 덥석 먹어", "Throw out | 버려", "Red | 빨강"):
        assert any(row.startswith(line) for row in lines), line


def test_vocab_lines_follow_the_level():
    level1 = vocab_lines(BANK, 1)
    assert "Kick the ball | 공을 차" in level1 and "Red | 빨강" not in level1
    assert len(level1.splitlines()) == len(BANK.up_to(1))


def test_catalog_summary_lists_what_the_llm_may_use():
    text = catalog_summary(CATALOG)
    for actor in ("chomp", "dino"):
        assert actor in text
    for action in ("turn_around", "throw_out", "put_on", "bark", "spin"):
        assert action in text
    assert "hatch" not in text  # 부화는 서버가 한다
    for face in CATALOG.actor_faces("dino"):
        assert face in text
    assert "egg: color, size" in text and "socks: color, wearable" in text and "apple: food" in text
    assert ", ".join(CATALOG.colors) in text and ", ".join(CATALOG.sizes) in text
    for kind in CATALOG.objects:
        assert f"- {kind}" in text


def test_session_prompt_has_vocab_and_catalog(orch_m2, llm):
    orch_m2.start_session("s")
    prompt = llm.system_prompt
    assert vocab_lines(BANK, 2) in prompt and catalog_summary(CATALOG) in prompt
    assert "{VOCAB}" not in prompt and "{CATALOG}" not in prompt
    assert '{"say": "...", "recast": null' in prompt  # 템플릿 속 JSON 중괄호는 그대로


# ---------------------------------------------------------------- prompts/chomp_system.md


SCHEMA_LINE = ('{"say": "...", "recast": null, "choice": null, "korean_hint": null, "ops": [], "mood": null, '
               '"utterance": {"lang": "en", "words_used": [], "errors": []}}')


def test_prompt_keeps_the_rules():
    for rule in (
        "Say: red egg!",           # 한국어 입력: 장면은 그대로, 따라 할 영어 한 문장
        '"ops": []',
        "→ English",               # korean_hint 모양 "뜻 → English sentence"
        'Never say "Wrong" or "No"',
        '"Good job!"', '"Excellent!"', '"Thumbs up!"',
        "Your turn!",
        "new task",                # 아이가 하지 않은 말에 새 할 일을 붙이지 않는다
        "only what the child just said",
        "at most 15 words",
        SCHEMA_LINE,
    ):
        assert rule in TEMPLATE, rule
    assert TEMPLATE.count("{VOCAB}") == 1 and TEMPLATE.count("{CATALOG}") == 1


def prompt_examples():
    """프롬프트의 예시 쌍: "User: {...}" 줄과 그 다음 답 줄."""
    lines = [line for line in TEMPLATE.splitlines() if line.strip()]
    pairs = []
    for i, line in enumerate(lines):
        if line.startswith("User: "):
            pairs.append((json.loads(line[len("User: "):]), json.loads(lines[i + 1])))
    return pairs


def test_prompt_examples_are_replies_the_validator_keeps():
    pairs = prompt_examples()
    assert len(pairs) >= 3
    for user, answer in pairs:
        reply, issues = validate_reply(answer, turn_id=1, catalog=CATALOG, scene=user["scene"],
                                       applied_ops=user["applied_ops"], input_has_hangul=has_hangul(user["heard"][0]))
        assert issues == [] and reply == answer, (user["heard"], issues)
        assert len(answer["say"].split()) <= 15 and not has_hangul(answer["say"])
        assert answer["korean_hint"] and " → " in answer["korean_hint"]
    assert any(has_hangul(user["heard"][0]) and answer["ops"] == [] for user, answer in pairs)
    assert any(answer["ops"] for _, answer in pairs)  # LLM 이 장면을 바꾸는 예시도 있다


def sentences(say):
    """대사를 문장으로 나눈다: . ! ? 뒤의 공백에서 끊는다("Say: red egg!" 는 한 문장)."""
    return [part for part in re.split(r"(?<=[.!?])\s+", say.strip()) if part]


def test_prompt_examples_follow_the_one_or_two_sentence_rule():
    # 규칙과 예시가 같은 말을 한다: 짧은 문장 한두 개, 리캐스트만 끝에 "Your turn!" 을 더할 수 있다
    assert 'One or two short sentences; a recast may add "Your turn!" at the end.' in TEMPLATE
    for user, answer in prompt_examples():
        parts = sentences(answer["say"])
        if answer["recast"] is not None and parts[-1] == "Your turn!":
            parts = parts[:-1]
        assert 1 <= len(parts) <= 2, (user["heard"], answer["say"])
        assert "Your turn!" not in parts, answer["say"]  # "Your turn!" 은 리캐스트의 끝에만


def test_prompt_names_the_only_answers_that_ask_the_child_to_speak():
    # "다음에 할 말을 시키지 마" 의 예외는 리캐스트("Your turn!"), 한국어("Say: ..."), 고르기 질문뿐이다
    assert "Do not tell the child what to do or say next" in TEMPLATE
    assert ('Only three answers may ask the child to say something: the recast with "Your turn!", '
            'the Korean case with "Say: ...", and the choice question below.') in TEMPLATE


def test_prompt_names_the_hatch_turn_exception_to_the_scene_rule(tmp_path):
    # 부화 턴의 scene 에는 이미 알이 없다(턴 메시지가 그렇게 보낸다). 프롬프트의 "BEFORE" 규칙도 이 예외를 말한다.
    assert ('One exception: when "applied_ops" starts with the hatch, the egg is already gone from "scene".'
            in TEMPLATE)
    orch, llm = make_m2(tmp_path, [reply_json(say="Baby Dino is here!")])
    turn(orch, "s", 1, ["i like dinosaurs"])
    sent = json.loads(llm.calls[0])
    assert sent["applied_ops"][0] == HATCH and "egg1" not in [item.get("id") for item in sent["scene"]]


def test_prompt_states_the_id_rules_the_validator_uses():
    assert '"scene" lists what was on screen BEFORE "applied_ops"' in TEMPLATE
    assert '"ref" in "place": a thing, never "chomp" or "dino".' in TEMPLATE
    assert 'an id that "applied_ops" added' in TEMPLATE
    assert 'Things that "applied_ops" removed are gone.' in TEMPLATE
    # 프롬프트가 말한 대로 검증기도 한다: applied_ops 가 만든 id 는 대상이 되고, 캐릭터는 ref 가 못 된다
    applied = [{"op": "add", "kind": "ball", "id": "ball1", "count": 1}]
    scene = [{"id": "chomp", "kind": "chomp"}, {"id": "dino", "kind": "dino"}, {"id": "tree1", "kind": "tree"}]
    on_tree = [{"op": "move", "target": "ball1", "place": {"rel": "on", "ref": "tree1"}}]
    reply, _ = validate_reply({"say": "Up it goes!", "ops": on_tree}, turn_id=1, catalog=CATALOG, scene=scene,
                              applied_ops=applied)
    assert reply["ops"] == on_tree
    on_dino = [{"op": "move", "target": "ball1", "place": {"rel": "on", "ref": "dino"}}]
    reply, _ = validate_reply({"say": "Up it goes!", "ops": on_dino}, turn_id=1, catalog=CATALOG, scene=scene,
                              applied_ops=applied)
    assert reply["ops"] == []
    gone = [{"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}]
    reply, _ = validate_reply({"say": "Kick!", "ops": gone}, turn_id=1, catalog=CATALOG, scene=scene,
                              applied_ops=[*applied, {"op": "remove", "target": "ball1"}])
    assert reply["ops"] == []
