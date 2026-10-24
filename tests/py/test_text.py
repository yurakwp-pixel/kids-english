"""server/text.py: 아이 말(음성인식 결과)을 다루는 작은 글자 도구."""
import pytest

from server.text import FILLERS, clean_text, has_hangul, is_noise, normalize, scrub_surrogates


def test_normalize_and_hangul():
    assert normalize("  Kick, the BALL!! ") == "kick the ball"
    assert has_hangul("빨간 알") and not has_hangul("red egg")


@pytest.mark.parametrize("raw, expected", [
    ("Let's dance.", "lets dance"),          # 아포스트로피는 지운다(띄우지 않는다)
    ("Let’s   dance!", "lets dance"),   # 휘어진 아포스트로피도
    ("thumbs-up", "thumbs up"),              # 하이픈과 그 밖의 구두점은 띄어쓰기로
    ("two\tblue\nballs", "two blue balls"),
    ("Hello, Chomp? 😀", "hello chomp"),     # 기호(이모지)도 지운다
    ("ｋｉｃｋ", "kick"),                    # 전각 글자는 보통 글자로
    ("빨간 알!", "빨간 알"),
    ("5 eggs", "5 eggs"),
    ("", ""),
    ("...", ""),
])
def test_normalize_cases(raw, expected):
    assert normalize(raw) == expected


# 단어장 accept 의 물건 자리 표시 `{object}` 는 normalize 를 지나도 남는다(Task 10, 결정 R2). 그래야
# accept[0] == normalize(en) 이 "Throw out the {object}" 에도 성립한다. 중괄호는 이 토큰일 때만 남는다.
@pytest.mark.parametrize("raw, expected", [
    ("Throw out the {object}!", "throw out the {object}"),
    ("Chomp the {object}", "chomp the {object}"),
    ("  KICK   {Object} ", "kick {object}"),       # 대소문자는 보통 글자처럼 소문자로
    ("kick the{object}", "kick the {object}"),     # 앞뒤가 붙어 있어도 따로 센다(원래 괄호는 띄어쓰기였다)
    ("{object}, please!", "{object} please"),
    ("put on your {object}{object}", "put on your {object} {object}"),
    ("｛object｝", "{object}"),                       # 전각 괄호도 NFKC 를 거쳐 같은 토큰
    ("{object}", "{object}"),
])
def test_normalize_keeps_the_object_placeholder(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("{foo} bar", "foo bar"),                      # 다른 중괄호 말은 전과 같이 구두점
    ("{object", "object"),
    ("object}", "object"),
    ("(object)", "object"),
    ("[object]", "object"),
    ("{ object }", "object"),
    ("{objects}", "objects"),
    ("{object }", "object"),
])
def test_normalize_still_strips_every_other_brace(raw, expected):
    assert normalize(raw) == expected


def test_normalize_is_idempotent_with_the_placeholder():
    for raw in ("Throw out the {object}!", "Chomp the {object}", "Let's clap", "{foo} {object}"):
        once = normalize(raw)
        assert normalize(once) == once


def test_normalize_non_string_is_empty():
    assert normalize(None) == "" and normalize(3) == ""


@pytest.mark.parametrize("text", ["ㅋㅋ", "가", "힣", "ᄀ", "ﾡ", "red 알"])
def test_has_hangul_covers_syllables_jamo_and_halfwidth(text):
    assert has_hangul(text)


@pytest.mark.parametrize("text", ["", "red egg", "こんにちは", "你好", None, 7])
def test_has_hangul_false_for_others(text):
    assert not has_hangul(text)


def test_fillers_are_exactly_the_brief_set():
    assert FILLERS == {"uh", "um", "hmm", "ah", "er"}


@pytest.mark.parametrize("text", ["", "   ", "uh", "Um...", "hmm, uh?", "ER", None])
def test_is_noise_true_for_empty_or_only_fillers(text):
    assert is_noise(text)


@pytest.mark.parametrize("text", ["uh oh", "um jump", "hello", "빨간 알", "a"])
def test_is_noise_false_when_a_real_word_is_there(text):
    assert not is_noise(text)


def test_scrub_surrogates_replaces_lone_surrogates_only():
    assert scrub_surrogates("hi \ud800 there \udfff") == "hi � there �"
    assert scrub_surrogates("멍멍 😀") == "멍멍 😀"
    scrub_surrogates("x\ud83d").encode("utf-8")  # 더는 인코딩 오류가 나지 않는다


def test_clean_text_squashes_scrubs_and_caps():
    assert clean_text("  kick \n the   ball ") == "kick the ball"
    assert clean_text("a\ud800b") == "a�b"
    assert clean_text("x" * 500, max_chars=10) == "x" * 10
    assert clean_text(None) == "" and clean_text(["no"]) == ""
