import re

import pytest

from server.canned import fallback_reply, praise

NEGATIVE = re.compile(r"\b(wrong|no|not|bad|can't|cannot|don't|fail)\b", re.IGNORECASE)


def test_praise_cycles():
    assert praise(0) == "Good job!" and praise(5) == "Good job!" and praise(2) == "Thumbs up!"


def test_praise_covers_all_five_in_order():
    assert [praise(i) for i in range(5)] == ["Good job!", "Excellent!", "Thumbs up!", "Wow!", "Well done!"]


@pytest.mark.parametrize("reason", ["timeout", "error", "unclear"])
def test_fallback_reply_is_complete_and_english(reason):
    r = fallback_reply(reason, 3)
    assert set(r) >= {"say", "recast", "choice", "korean_hint", "ops", "mood", "utterance"}
    assert r["ops"] == [] and not re.search(r"[가-힣]", r["say"]) and len(r["say"].split()) <= 15


@pytest.mark.parametrize("reason", ["timeout", "error", "unclear"])
def test_fallback_reply_matches_spec_shape(reason):
    r = fallback_reply(reason, 0)
    assert list(r)[0] == "say"  # say 를 첫 키로 둔다(스펙 5.5)
    assert r["recast"] is None and r["choice"] is None and r["mood"] is None
    assert r["utterance"] == {"lang": "unknown", "words_used": [], "errors": []}
    assert isinstance(r["say"], str) and r["say"].strip()
    # 한글은 korean_hint 에만, "뜻 → English sentence" 형태
    assert re.search(r"[가-힣]", r["korean_hint"])
    meaning, arrow, english = r["korean_hint"].partition(" → ")
    assert arrow and meaning and english and not re.search(r"[가-힣]", english)


@pytest.mark.parametrize("reason", ["timeout", "error", "unclear"])
@pytest.mark.parametrize("turn_id", [0, 1, 2, 7, 100])
def test_fallback_reply_is_kid_safe(reason, turn_id):
    r = fallback_reply(reason, turn_id)
    assert not NEGATIVE.search(r["say"])
    assert "<" not in r["say"] and "http" not in r["say"].lower()
    assert not re.search(r"[가-힣]", r["say"]) and len(r["say"].split()) <= 15


def test_fallback_reply_unclear_asks_to_say_it_again():
    assert "again" in fallback_reply("unclear", 0)["say"].lower()
    assert fallback_reply("unclear", 0)["korean_hint"] == "다시 말해 볼까? → Say it again!"


@pytest.mark.parametrize("reason", ["timeout", "error"])
def test_fallback_reply_timeout_and_error_try_again(reason):
    r = fallback_reply(reason, 0)
    assert "try" in r["say"].lower() and "again" in r["say"].lower()


def test_fallback_reply_returns_fresh_objects():
    a = fallback_reply("error", 1)
    a["ops"].append("x")
    a["utterance"]["words_used"].append("y")
    b = fallback_reply("error", 1)
    assert b["ops"] == [] and b["utterance"]["words_used"] == []


def test_fallback_reply_unknown_reason_still_answers():
    r = fallback_reply("surprise", 0)  # type: ignore[arg-type]
    assert r["ops"] == [] and r["say"] and "again" in r["say"].lower()
