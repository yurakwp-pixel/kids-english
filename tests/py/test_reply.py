"""답 파싱(parse_reply_text)과 검증기(validate_reply).

검증기는 LLM 과 아이 화면 사이의 안전망이다. LLM 이 무엇을 내보내든(코드펜스, 잘린 JSON, 엉뚱한 타입,
한글 say, 알 수 없는 키) 서버 밖으로 나가는 답은 언제나 shared/schemas/turn_reply.schema.json 을 만족해야 한다.
"""
import json
import random
import re
import time
from pathlib import Path

import jsonschema
import pytest

from server.canned import fallback_reply
from server.reply import parse_reply_text, validate_reply

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "shared" / "schemas" / "turn_reply.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)  # 한 번만 만든다: jsonschema.validate() 는 부를 때마다 스키마를 다시 점검해 느리다
FIXTURE = ROOT / "tests" / "fixtures" / "llm_outputs.jsonl"
KEYS = ["say", "recast", "choice", "korean_hint", "ops", "mood", "utterance"]
BAD_SAY = re.compile(r"[가-힣<]|https?://")


def good(**changes):
    """스펙 5.5 예시와 같은 모양의 정상 답. changes 로 필드를 바꾼다."""
    reply = {
        "say": "Kick the ball! Wow, so far!",
        "recast": None,
        "choice": None,
        "korean_hint": "공을 차 보자 → Kick the ball!",
        "ops": [],
        "mood": {"actor": "dino", "face": "excited"},
        "utterance": {"lang": "en", "words_used": ["kick-the-ball"], "errors": []},
    }
    reply.update(changes)
    return reply


def fixture_cases():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- parse_reply_text


def test_parse_reply_text_handles_fences_and_prose():
    assert parse_reply_text('Sure!\n```json\n{"say":"Hi!"}\n```') == {"say": "Hi!"}
    assert parse_reply_text("no json here") is None


def test_parse_reply_text_plain_and_nested_objects():
    text = '{"say":"Hi!","utterance":{"lang":"en","words_used":[],"errors":[]}}'
    assert parse_reply_text(text) == json.loads(text)
    assert parse_reply_text('  \n {"say":"Hi!"}  trailing words') == {"say": "Hi!"}


def test_parse_reply_text_takes_first_complete_object():
    assert parse_reply_text('{"say":"One!"} then {"say":"Two!"}') == {"say": "One!"}


def test_parse_reply_text_skips_braces_in_prose():
    assert parse_reply_text('Here {is} it, {really}: {"say":"Hi!"}') == {"say": "Hi!"}


def test_parse_reply_text_ignores_braces_inside_strings():
    assert parse_reply_text('x {"say":"a } b { c \\" d"} y') == {"say": 'a } b { c " d'}


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "Sorry, I can't help with that.",
    "[1, 2, 3]",
    '{"say":"Hi',                                           # 잘림
    '{"say":"Hi","utterance":{"lang":"en"}',                # 바깥 객체만 안 닫힘: 안쪽 조각을 답으로 착각하지 않는다
    '{"say": "Hi",}',                                       # 문법 오류
    "{'say': 'Hi'}",                                        # 작은따옴표
])
def test_parse_reply_text_returns_none_without_a_complete_object(text):
    assert parse_reply_text(text) is None


@pytest.mark.parametrize("value", [None, 5, b'{"say":"Hi"}', ["x"], {"say": "Hi"}])
def test_parse_reply_text_non_string_input_is_none(value):
    assert parse_reply_text(value) is None


def test_parse_reply_text_survives_absurd_nesting():
    deep = '{"a":' * 5000 + "1" + "}" * 5000
    assert parse_reply_text(deep) is None


# ---------------------------------------------------------------- say


def test_say_rules_fall_back_to_canned():
    for bad in ["안녕 Chomp!", "a " * 20, "<b>Hi</b>", "Go to https://x.com"]:
        r, issues = validate_reply({"say": bad}, turn_id=1)
        assert not re.search(r"[가-힣<]|https?://", r["say"]) and len(r["say"].split()) <= 15 and issues


def test_unusable_say_is_the_canned_error_line_and_flagged():
    r, issues = validate_reply({"say": "안녕 Chomp!"}, turn_id=3)
    assert r["say"] == fallback_reply("error", 3)["say"]
    assert "say_hangul" in issues and "say_replaced" in issues


@pytest.mark.parametrize("bad, issue", [
    (None, "say_not_string"),
    (42, "say_not_string"),
    (["Hi!"], "say_not_string"),
    ({"text": "Hi!"}, "say_not_string"),
    ("", "say_empty"),
    (" \n\t ", "say_empty"),
    ("ㅋㅋㅋ", "say_hangul"),                                   # 자모만 있어도 한글
    ("Hello 안녕", "say_hangul"),
    ("a < b", "say_markup"),
    ("Visit www.chomp.fun now!", "say_url"),
    ("Visit chomp.com now!", "say_url"),
    ("Open ftp://files/x please", "say_url"),
    ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen", "say_too_long"),
    ("x" * 201, "say_too_long"),                                 # 15단어 이하여도 글자 수가 말이 안 되면 거른다
])
def test_unusable_say_values_are_replaced(bad, issue):
    r, issues = validate_reply({"say": bad}, turn_id=1)
    assert r["say"] == fallback_reply("error", 1)["say"]
    assert issue in issues and "say_replaced" in issues


def test_missing_say_is_replaced():
    r, issues = validate_reply({"recast": None}, turn_id=1)
    assert r["say"] == fallback_reply("error", 1)["say"]
    assert "say_missing" in issues and "say_replaced" in issues


def test_say_keeps_good_sentences_and_drops_the_bad_one():
    r, issues = validate_reply({"say": "Good job! 잘했어! Kick the ball!"}, turn_id=1)
    assert r["say"] == "Good job! Kick the ball!"
    assert issues == ["say_hangul", "utterance_missing"]


def test_say_too_long_drops_the_trailing_sentence():
    # 실제 haiku 출력에서 본 모양: 뒤에 엉뚱한 "Your turn: ..." 문장이 붙는다.
    say = "Kick the ball! Wow, so close! Your turn: Baby Dino, clap and jump and dance with me!"
    r, issues = validate_reply({"say": say}, turn_id=1)
    assert r["say"] == "Kick the ball! Wow, so close!"
    assert "say_too_long" in issues and "say_replaced" not in issues


def test_say_of_exactly_fifteen_words_is_kept():
    say = " ".join(["word"] * 14) + " end!"
    r, issues = validate_reply({"say": say}, turn_id=1)
    assert r["say"] == say and "say_too_long" not in issues


def test_say_whitespace_is_collapsed():
    r, _ = validate_reply({"say": "  Hi\n\n there!  \t Go "}, turn_id=1)
    assert r["say"] == "Hi there! Go"


def test_replaced_say_takes_the_matching_canned_hint_and_drops_the_choice():
    r, _ = validate_reply(
        {"say": "안녕!", "korean_hint": "공을 차 보자 → Kick the ball!", "choice": ["Kick", "Jump"]},
        turn_id=2,
    )
    canned = fallback_reply("error", 2)
    assert r["say"] == canned["say"] and r["korean_hint"] == canned["korean_hint"]
    assert r["choice"] is None  # 질문(say)이 사라졌으니 보기만 남기지 않는다


# ---------------------------------------------------------------- korean_hint


def test_hangul_allowed_only_in_korean_hint():
    r, _ = validate_reply({"say": "Kick!", "korean_hint": "공을 차 보자 → Kick the ball!"}, turn_id=1)
    assert r["korean_hint"].startswith("공을")


def test_korean_hint_without_arrow_is_fine():
    r, issues = validate_reply({"say": "Kick!", "korean_hint": "공을 차!"}, turn_id=1)  # 스펙 5.5 예시 모양
    assert r["korean_hint"] == "공을 차!" and "korean_hint_invalid" not in issues


@pytest.mark.parametrize("bad, issue", [
    (42, "korean_hint_invalid"),
    (["공"], "korean_hint_invalid"),
    ("<b>공</b> → Ball", "korean_hint_unsafe"),
    ("공 → see https://x.com", "korean_hint_unsafe"),
    ("공 " * 100, "korean_hint_unsafe"),
])
def test_bad_korean_hint_is_dropped(bad, issue):
    r, issues = validate_reply({"say": "Kick!", "korean_hint": bad}, turn_id=1)
    assert r["korean_hint"] is None and issue in issues


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_korean_hint_is_none_without_complaint(empty):
    r, issues = validate_reply({"say": "Kick!", "korean_hint": empty}, turn_id=1)
    assert r["korean_hint"] is None and not [i for i in issues if i.startswith("korean_hint")]


# ---------------------------------------------------------------- recast


def test_recast_valid_is_kept_and_extra_keys_dropped():
    r, issues = validate_reply(
        {"say": "Kick the ball! Your turn!", "recast": {"model": " Kick the ball! ", "ask_repeat": True, "x": 1}},
        turn_id=1,
    )
    assert r["recast"] == {"model": "Kick the ball!", "ask_repeat": True}
    assert not [i for i in issues if i.startswith("recast")]


def test_recast_missing_ask_repeat_defaults_to_true():
    r, issues = validate_reply({"say": "Hi!", "recast": {"model": "Kick the ball!"}}, turn_id=1)
    assert r["recast"] == {"model": "Kick the ball!", "ask_repeat": True}
    assert "recast_ask_repeat_defaulted" in issues


@pytest.mark.parametrize("bad", [
    "Kick the ball!",
    5,
    ["Kick the ball!"],
    {},
    {"model": ""},
    {"model": 7, "ask_repeat": True},
    {"model": "공을 차!", "ask_repeat": True},
    {"model": "<i>Kick</i>", "ask_repeat": True},
    {"model": "see https://x.com", "ask_repeat": True},
    {"model": "word " * 16, "ask_repeat": True},
])
def test_recast_invalid_becomes_none(bad):
    r, issues = validate_reply({"say": "Hi!", "recast": bad}, turn_id=1)
    assert r["recast"] is None and "recast_invalid" in issues


# ---------------------------------------------------------------- choice


def test_choice_is_trimmed_to_three():
    r, issues = validate_reply({"say": "Kick or jump?", "choice": ["Kick", "Jump", "Clap", "Dance", "Sleep"]}, turn_id=1)
    assert r["choice"] == ["Kick", "Jump", "Clap"] and "choice_trimmed" in issues


def test_choice_of_two_and_three_is_kept():
    for options in (["Kick", "Jump"], ["Kick", "Jump", "Clap"]):
        r, issues = validate_reply({"say": "Which one?", "choice": options}, turn_id=1)
        assert r["choice"] == options and not [i for i in issues if i.startswith("choice")]


@pytest.mark.parametrize("raw", [["Kick"], [], ["Kick", "kick"], ["Kick", 5], ["Kick", "점프"]])
def test_choice_with_fewer_than_two_usable_options_is_none(raw):
    r, _ = validate_reply({"say": "Which one?", "choice": raw}, turn_id=1)
    assert r["choice"] is None


@pytest.mark.parametrize("raw", ["Kick or jump", 3, {"a": "b"}])
def test_choice_not_a_list_is_none(raw):
    r, issues = validate_reply({"say": "Which one?", "choice": raw}, turn_id=1)
    assert r["choice"] is None and "choice_invalid" in issues


def test_choice_drops_bad_items_then_trims():
    r, issues = validate_reply(
        {"say": "Which one?", "choice": ["Kick", "점프", 7, None, "Jump", "Clap", "Dance"]}, turn_id=1
    )
    assert r["choice"] == ["Kick", "Jump", "Clap"]
    assert "choice_item_dropped" in issues and "choice_trimmed" in issues


# ---------------------------------------------------------------- ops (M1: 카탈로그 없음)


def test_m1_drops_all_ops_without_catalog():
    r, issues = validate_reply({"say": "Ok!", "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1)
    assert r["ops"] == [] and "ops_dropped_no_catalog" in issues


def test_no_ops_means_no_ops_issue():
    for body in ({"say": "Ok!"}, {"say": "Ok!", "ops": []}, {"say": "Ok!", "ops": None}):
        r, issues = validate_reply(body, turn_id=1)
        assert r["ops"] == [] and not [i for i in issues if i.startswith("ops")]


@pytest.mark.parametrize("raw", ["add an egg", 3, {"op": "add"}])
def test_ops_not_a_list_is_empty(raw):
    r, issues = validate_reply({"say": "Ok!", "ops": raw}, turn_id=1)
    assert r["ops"] == [] and "ops_invalid" in issues


def test_hangul_input_never_keeps_ops():
    r, issues = validate_reply(
        {"say": "Say: red egg!", "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1, input_has_hangul=True
    )
    assert r["ops"] == [] and "ops_dropped_hangul" in issues


def test_ops_are_dropped_even_if_a_catalog_object_is_passed_before_task_12():
    # 카탈로그 검사는 Task 12 에서 붙는다. 그 전에는 카탈로그가 있어도 검증 안 된 op 를 화면에 내보내지 않는다.
    r, issues = validate_reply({"say": "Ok!", "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1, catalog=object())
    assert r["ops"] == [] and any(i.startswith("ops_") for i in issues)


# ---------------------------------------------------------------- mood


def test_mood_valid_is_normalised():
    r, issues = validate_reply({"say": "Hi!", "mood": {"actor": " Chomp ", "face": "Happy", "x": 1}}, turn_id=1)
    assert r["mood"] == {"actor": "chomp", "face": "happy"} and "mood_invalid" not in issues


@pytest.mark.parametrize("bad", [
    "happy",
    5,
    [],
    {},
    {"actor": "chomp"},
    {"actor": "robot", "face": "happy"},
    {"actor": "chomp", "face": ""},
    {"actor": "chomp", "face": "so very happy indeed"},
    {"actor": "chomp", "face": 7},
    {"actor": "chomp", "face": "<b>"},
])
def test_mood_invalid_becomes_none(bad):
    r, issues = validate_reply({"say": "Hi!", "mood": bad}, turn_id=1)
    assert r["mood"] is None and "mood_invalid" in issues


# ---------------------------------------------------------------- utterance


def test_missing_utterance_gets_the_default():
    r, issues = validate_reply({"say": "Hi!"}, turn_id=1)
    assert r["utterance"] == {"lang": "unknown", "words_used": [], "errors": []} and "utterance_missing" in issues


@pytest.mark.parametrize("bad", ["en", 5, [], ["en"]])
def test_utterance_not_an_object_gets_the_default(bad):
    r, issues = validate_reply({"say": "Hi!", "utterance": bad}, turn_id=1)
    assert r["utterance"] == {"lang": "unknown", "words_used": [], "errors": []} and "utterance_invalid" in issues


@pytest.mark.parametrize("lang", ["en", "ko", "mixed", "unknown"])
def test_utterance_lang_values_are_kept(lang):
    r, issues = validate_reply({"say": "Hi!", "utterance": {"lang": lang, "words_used": [], "errors": []}}, turn_id=1)
    assert r["utterance"]["lang"] == lang and issues == []


@pytest.mark.parametrize("lang", ["French", "", None, 3, ["en"]])
def test_utterance_bad_lang_is_unknown(lang):
    r, issues = validate_reply({"say": "Hi!", "utterance": {"lang": lang, "words_used": [], "errors": []}}, turn_id=1)
    assert r["utterance"]["lang"] == "unknown" and "utterance_lang_invalid" in issues


def test_utterance_lang_is_case_insensitive():
    r, _ = validate_reply({"say": "Hi!", "utterance": {"lang": " EN ", "words_used": [], "errors": []}}, turn_id=1)
    assert r["utterance"]["lang"] == "en"


def test_utterance_lists_are_cleaned():
    body = {"say": "Hi!", "utterance": {
        "lang": "en",
        "words_used": ["kick-the-ball", 5, None, "", "clap"],
        "errors": [{"said": "kick ball", "better": "kick the ball", "n": 3}, "oops", 7, {}],
    }}
    r, issues = validate_reply(body, turn_id=1)
    assert r["utterance"]["words_used"] == ["kick-the-ball", "clap"]
    assert r["utterance"]["errors"] == [{"said": "kick ball", "better": "kick the ball"}]
    assert "words_used_fixed" in issues and "errors_fixed" in issues


@pytest.mark.parametrize("raw", ["kick", 3, {"a": 1}, None])
def test_utterance_lists_not_lists_become_empty(raw):
    r, _ = validate_reply({"say": "Hi!", "utterance": {"lang": "en", "words_used": raw, "errors": raw}}, turn_id=1)
    assert r["utterance"]["words_used"] == [] and r["utterance"]["errors"] == []


def test_utterance_lists_are_capped():
    body = {"say": "Hi!", "utterance": {"lang": "en", "words_used": [f"w{i}" for i in range(50)],
                                         "errors": [{"said": str(i)} for i in range(50)]}}
    r, _ = validate_reply(body, turn_id=1)
    assert len(r["utterance"]["words_used"]) <= 20 and len(r["utterance"]["errors"]) <= 10


def test_utterance_absurdly_long_strings_are_dropped():
    body = {"say": "Hi!", "utterance": {"lang": "en", "words_used": ["ok", "w" * 500],
                                         "errors": [{"said": "s" * 500, "better": "fine"}]}}
    r, _ = validate_reply(body, turn_id=1)
    assert r["utterance"]["words_used"] == ["ok"] and r["utterance"]["errors"] == [{"better": "fine"}]


# ---------------------------------------------------------------- 전체 모양


def test_valid_reply_passes_through_unchanged():
    r, issues = validate_reply(good(), turn_id=1)
    assert r == good() and issues == []


def test_output_has_exactly_the_schema_keys_in_order():
    r, _ = validate_reply({**good(), "emotion": "happy", "debug": {"x": 1}}, turn_id=1)
    assert list(r) == KEYS  # say 가 첫 키, 모르는 키는 버린다


def test_output_does_not_alias_the_input():
    body = good(utterance={"lang": "en", "words_used": ["a"], "errors": [{"said": "x"}]})
    r, _ = validate_reply(body, turn_id=1)
    r["utterance"]["words_used"].append("b")
    r["utterance"]["errors"][0]["said"] = "changed"
    assert body["utterance"]["words_used"] == ["a"] and body["utterance"]["errors"] == [{"said": "x"}]


@pytest.mark.parametrize("reason", ["timeout", "error", "unclear"])
def test_canned_replies_are_fixed_points_of_the_validator(reason):
    canned = fallback_reply(reason, 4)
    r, issues = validate_reply(canned, turn_id=4)
    assert r == canned and issues == []


@pytest.mark.parametrize("bad", [None, "text", 3, [], [{"say": "Hi!"}], True])
def test_non_object_reply_is_the_canned_error_reply(bad):
    r, issues = validate_reply(bad, turn_id=7)
    assert r == fallback_reply("error", 7) and issues == ["reply_not_object"]
    jsonschema.validate(r, SCHEMA)


@pytest.mark.parametrize("turn_id", [None, "x", 1.5, -3, 10**12])
def test_odd_turn_ids_do_not_crash(turn_id):
    r, _ = validate_reply({"say": "안녕"}, turn_id=turn_id)
    jsonschema.validate(r, SCHEMA)


def test_huge_strings_are_rejected_quickly():
    # URL 정규식을 아주 긴 글에 돌리면 글자 수의 제곱만큼 느려진다(5만 자에 약 18초). 길이부터 보고 거른다.
    huge = "x" * 50_000
    started = time.perf_counter()
    r, issues = validate_reply(
        {"say": huge, "recast": {"model": huge, "ask_repeat": True}, "choice": [huge, huge, huge],
         "korean_hint": huge, "utterance": {"lang": "en", "words_used": [huge], "errors": [{"said": huge}]}},
        turn_id=1,
    )
    r2, _ = validate_reply({"say": "Hi!", "recast": {"model": huge}, "choice": [huge, huge], "korean_hint": huge},
                           turn_id=1)
    assert time.perf_counter() - started < 3
    assert r["say"] == fallback_reply("error", 1)["say"] and "say_too_long" in issues
    assert r2["recast"] is None and r2["choice"] is None and r2["korean_hint"] is None


# ---------------------------------------------------------------- 스키마

def test_schema_is_a_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("name, mutate", [
    ("say_hangul", lambda r: r.update(say="안녕!")),
    ("say_markup", lambda r: r.update(say="<b>Hi</b>")),
    ("say_url", lambda r: r.update(say="see https://x.com")),
    ("say_16_words", lambda r: r.update(say=" ".join(["w"] * 16))),
    ("say_empty", lambda r: r.update(say="")),
    ("say_number", lambda r: r.update(say=5)),
    ("missing_key", lambda r: r.pop("mood")),
    ("extra_key", lambda r: r.update(debug=1)),
    ("recast_string", lambda r: r.update(recast="Kick!")),
    ("recast_no_flag", lambda r: r.update(recast={"model": "Kick!"})),
    ("choice_one", lambda r: r.update(choice=["Kick"])),
    ("choice_four", lambda r: r.update(choice=["a", "b", "c", "d"])),
    ("choice_hangul", lambda r: r.update(choice=["Kick", "점프"])),
    ("hint_markup", lambda r: r.update(korean_hint="<b>공</b>")),
    ("ops_five", lambda r: r.update(ops=[{"op": "add"}] * 5)),
    ("ops_string", lambda r: r.update(ops="add")),
    ("ops_unknown_kind", lambda r: r.update(ops=[{"op": "explode"}])),
    ("mood_unknown_actor", lambda r: r.update(mood={"actor": "robot", "face": "happy"})),
    ("utterance_bad_lang", lambda r: r["utterance"].update(lang="fr")),
    ("utterance_missing_errors", lambda r: r["utterance"].pop("errors")),
])
def test_schema_rejects_what_the_validator_must_never_emit(name, mutate):
    reply = good()
    mutate(reply)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(reply, SCHEMA)


def test_schema_accepts_the_spec_example_and_all_nullable_fields():
    jsonschema.validate(good(ops=[{"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}]), SCHEMA)
    jsonschema.validate(good(korean_hint=None, mood=None, recast={"model": "Kick!", "ask_repeat": False},
                             choice=["Kick", "Jump"]), SCHEMA)


# ---------------------------------------------------------------- 퍼징


def test_fuzzed_outputs_always_match_schema():
    schema = json.loads(Path("shared/schemas/turn_reply.schema.json").read_text(encoding="utf-8"))
    for line in Path("tests/fixtures/llm_outputs.jsonl").read_text(encoding="utf-8").splitlines():
        obj = parse_reply_text(json.loads(line)["text"])
        r, _ = validate_reply(obj, turn_id=7)
        jsonschema.validate(r, schema)


def test_fixture_file_covers_the_required_failure_modes():
    cases = fixture_cases()
    assert len(cases) >= 12
    names = {c["name"] for c in cases}
    assert {"normal", "code_fence", "prose_around", "truncated_json", "say_missing", "say_number", "ops_string",
            "utterance_missing", "recast_string", "choice_five", "empty_string", "hangul_say"} <= names


@pytest.mark.parametrize("case", fixture_cases(), ids=lambda c: c["name"])
def test_every_fixture_line_yields_a_schema_valid_reply(case):
    r, issues = validate_reply(parse_reply_text(case["text"]), turn_id=7)
    jsonschema.validate(r, SCHEMA)
    assert list(r) == KEYS and not BAD_SAY.search(r["say"]) and len(r["say"].split()) <= 15
    assert r["ops"] == []  # M1: LLM 의 ops 는 화면에 나가지 않는다
    for issue in case.get("issues", []):
        assert issue in issues, f"{case['name']}: expected issue {issue!r}, got {issues}"
    if case.get("clean"):
        assert issues == [], f"{case['name']}: {issues}"


def test_fixture_normal_cases_keep_their_content():
    by_name = {c["name"]: c for c in fixture_cases()}
    for name in ("normal", "code_fence", "prose_around", "braces_in_prose", "extra_fields"):
        r, _ = validate_reply(parse_reply_text(by_name[name]["text"]), turn_id=7)
        assert r == good(), name
    r, _ = validate_reply(parse_reply_text(by_name["two_objects_first_wins"]["text"]), turn_id=7)
    assert r["say"] == "One!"
    r, _ = validate_reply(parse_reply_text(by_name["choice_five"]["text"]), turn_id=7)
    assert r["choice"] == ["Kick", "Jump", "Clap"]


# ---- 무작위 퍼징: 어떤 JSON 값이 어느 필드에 와도 스키마를 만족하고, 한 번 고친 답은 다시 고칠 것이 없다.

_JUNK = [
    None, True, False, 0, -1, 7, 3.5, 1e308, "", " ", "\n", "Hi!", "Kick the ball!", "안녕 Chomp!", "ㅋㅋ", "<b>x</b>",
    "http://x.io", "www.x.com", "word " * 30, "A. B. C. D. E. F. G. H.", "x" * 5000, [], [1, 2], ["a"], ["a", "b"],
    ["a", "b", "c", "d", "e"], ["a", 1, None, "가"], {}, {"op": "add"}, {"op": "add", "kind": "egg"},
    {"model": "Kick!", "ask_repeat": True}, {"actor": "dino", "face": "happy"},
    {"lang": "en", "words_used": [], "errors": []}, {"x": [{"y": None}]}, [[]], [{}],
]


def _junk(rng):
    return rng.choice(_JUNK)


def _mutated_reply(rng):
    reply = good(recast={"model": "Kick the ball!", "ask_repeat": True}, choice=["Kick", "Jump"],
                 ops=[{"op": "add", "kind": "egg"}])
    for key in list(reply):
        roll = rng.random()
        if roll < 0.35:
            reply[key] = _junk(rng)
        elif roll < 0.45:
            del reply[key]
    for sub, keys in (("recast", ("model", "ask_repeat")), ("mood", ("actor", "face")),
                      ("utterance", ("lang", "words_used", "errors"))):
        if isinstance(reply.get(sub), dict):
            for key in keys:
                if rng.random() < 0.3:
                    reply[sub][key] = _junk(rng)
    for extra in ("emotion", "debug", "say2"):
        if rng.random() < 0.2:
            reply[extra] = _junk(rng)
    return reply


def test_random_garbage_always_yields_schema_valid_and_stable_replies():
    rng = random.Random(20261022)
    for i in range(3000):
        body = _mutated_reply(rng)
        for catalog, hangul in ((None, False), (None, True), (object(), False)):
            r, _ = validate_reply(body, turn_id=i, catalog=catalog, input_has_hangul=hangul)
            VALIDATOR.validate(r)
            assert list(r) == KEYS and not BAD_SAY.search(r["say"]) and len(r["say"].split()) <= 15
            again, issues = validate_reply(r, turn_id=i)
            assert again == r and issues == [], (body, r, issues)


def test_random_non_object_values_yield_the_canned_reply():
    rng = random.Random(7)
    for i in range(200):
        value = rng.choice([v for v in _JUNK if not isinstance(v, dict)])
        r, issues = validate_reply(value, turn_id=i)
        VALIDATOR.validate(r)
        assert issues == ["reply_not_object"]
