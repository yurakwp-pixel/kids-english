"""턴 처리기(TurnOrchestrator), 세션 로그(SessionLog), 프롬프트 만들기(prompt_builder).

진짜 Claude 는 부르지 않는다. FakeLLM 이 정해 둔 답을 돌려준다(쿼터 사용 0).
"""
import json
import threading
from datetime import datetime
from pathlib import Path

import jsonschema
import pytest

import server.turn as turn_module
from server.llm import LLMBillingRefused, LLMError
from server.llm.fake import FakeLLM
from server.prompt_builder import NONE_YET, build_system_prompt, build_turn_message
from server.session_log import SessionLog
from server.turn import BadRequest, TurnOrchestrator, redact

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "prompts" / "chomp_system.md").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "shared" / "schemas" / "turn_reply.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
LINE_KEYS = ("kind", "turn_id", "source", "latency")


def make_orch(llm, tmp_path, timeout_s=8.0):
    return TurnOrchestrator(llm, SessionLog(tmp_path / "data" / "sessions"), TEMPLATE, timeout_s=timeout_s)


def req(session_id, turn_id, alts, **extra):
    """스펙 5.5 모양의 턴 요청. alts 는 인식 후보 글자들."""
    body = {
        "session_id": session_id,
        "turn_id": turn_id,
        "asr": {"engine": "webspeech", "lang": "en-US",
                "alternatives": [{"t": t, "c": round(0.9 - i * 0.1, 2)} for i, t in enumerate(alts)]},
        "hint_requested": False,
        "scene": [],
    }
    body.update(extra)
    return body


def log_path(tmp_path, session_id):
    return tmp_path / "data" / "sessions" / f"{session_id}.jsonl"


def log_events(tmp_path, session_id):
    return [json.loads(line) for line in log_path(tmp_path, session_id).read_text(encoding="utf-8").splitlines()]


def all_keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_keys(inner)


class SpyLLM(FakeLLM):
    """FakeLLM + 세션을 몇 번 열고 닫았는지 센다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.starts = 0
        self.ends = 0

    def start_session(self, system_prompt):
        self.starts += 1
        super().start_session(system_prompt)

    def end_session(self):
        self.ends += 1


# ---------------------------------------------------------------- brief Step 1


def test_turn_happy_path_llm(tmp_path):
    llm = FakeLLM(['{"say":"Hi! I am Chomp!","utterance":{"lang":"en","words_used":[],"errors":[]}}'])
    orch = make_orch(llm, tmp_path); orch.start_session("s1")
    lines = list(orch.handle(req("s1", 1, ["hello chomp"])))
    assert [line["kind"] for line in lines] == ["ack", "reply"]
    assert lines[1]["source"] == "llm" and lines[1]["say"] == "Hi! I am Chomp!"
    assert '"heard": ["hello chomp"]' in llm.calls[0]


@pytest.mark.parametrize("alts", [[], ["uh"], ["  "], ["um", "hmm"]])
def test_empty_or_filler_skips_llm(tmp_path, alts):
    llm = FakeLLM(["{}"]); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    reply = list(orch.handle(req("s1", 2, alts)))[-1]
    assert reply["source"] == "template" and llm.calls == []
    assert reply["say"] in ("Say it again! I'm listening.", "Oh! Say it one more time!")  # canned "unclear"
    assert reply["latency"]["llm_ms"] is None


def test_timeout_and_error_fall_back(tmp_path):
    for llm in [FakeLLM(["{}"], delay_s=0.2), FakeLLM([LLMError("dead")])]:
        orch = make_orch(llm, tmp_path, timeout_s=0.05); orch.start_session("s1")
        reply = list(orch.handle(req("s1", 3, ["jump"])))[-1]
        assert reply["source"] == "fallback" and reply["ops"] == []


def test_hangul_input_never_has_ops(tmp_path):
    llm = FakeLLM(['{"say":"Red egg? Say: red egg!","ops":[{"op":"add","kind":"egg"}]}'])
    orch = make_orch(llm, tmp_path); orch.start_session("s1")
    assert list(orch.handle(req("s1", 4, ["빨간 알"])))[-1]["ops"] == []


def test_turns_are_serialized(tmp_path):
    llm = FakeLLM(['{"say":"Ok!"}'], delay_s=0.05); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    # 두 스레드에서 동시에 handle → FakeLLM에 겹친 호출이 없는지(동시 실행 카운터 최대 1) 확인
    go = threading.Barrier(2)
    results = {}

    def run(turn_id):
        go.wait()
        results[turn_id] = list(orch.handle(req("s1", turn_id, ["jump"])))

    threads = [threading.Thread(target=run, args=(turn_id,)) for turn_id in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert llm.max_concurrent == 1
    assert len(llm.calls) == 2
    for turn_id, lines in results.items():
        assert [(line["kind"], line["turn_id"]) for line in lines] == [("ack", turn_id), ("reply", turn_id)]


def test_session_log_written(tmp_path):
    llm = FakeLLM(['{"say":"Hi! I am Chomp!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["hello chomp", "hello champ"])))
    orch.end_session("s1")
    events = log_events(tmp_path, "s1")
    assert [e["type"] for e in events] == ["session_start", "turn", "session_end"]
    turn = events[1]
    assert turn["turn_id"] == 1 and turn["heard"] == ["hello chomp", "hello champ"]
    assert turn["source"] == "llm" and turn["say"] == "Hi! I am Chomp!" and turn["ops"] == []
    assert set(turn["latency"]) == {"llm_ms", "total_ms"}
    assert datetime.fromisoformat(turn["ts"]).tzinfo is not None
    assert events[2]["turns"] == 1
    # 오디오는 저장하지 않는다: 음성인식 엔진 정보나 오디오 관련 필드가 로그에 없다.
    keys = [k.lower() for e in events for k in all_keys(e)]
    assert not any("audio" in k for k in keys) and "asr" not in keys and "engine" not in keys


@pytest.mark.parametrize("alts", [["hello chomp"], []])  # LLM 턴과 템플릿(조용함) 턴
@pytest.mark.parametrize("asked", [True, False])
def test_turn_log_records_hint_requested(tmp_path, alts, asked):
    # 스펙 4장: (?) 를 누른 것은 다음 턴 요청의 hint_requested:true 로 실려 기록된다
    orch = make_orch(FakeLLM(['{"say":"Hi!"}']), tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, alts, hint_requested=asked)))
    assert log_events(tmp_path, "s1")[-1]["hint_requested"] is asked


def test_hint_requested_must_be_exactly_true(tmp_path):
    orch = make_orch(FakeLLM(['{"say":"Hi!"}']), tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["hello"], hint_requested="yes")))
    assert log_events(tmp_path, "s1")[-1]["hint_requested"] is False


# ---------------------------------------------------------------- 답 줄의 모양


@pytest.mark.parametrize("script, alts, source", [
    (['{"say":"Hi! I am Chomp!"}'], ["hello"], "llm"),
    (["{}"], [], "template"),
    ([LLMError("x")], ["jump"], "fallback"),
    (["not json at all"], ["jump"], "fallback"),
    (['{"say":"빨간 알!","ops":[{"op":"add"}]}'], ["빨간 알"], "llm"),  # 한글 say 는 canned 로 바뀌어도 LLM 답이다
])
def test_reply_line_shape_and_schema(tmp_path, script, alts, source):
    orch = make_orch(FakeLLM(script), tmp_path); orch.start_session("s1")
    ack, reply = list(orch.handle(req("s1", 7, alts)))
    assert ack == {"kind": "ack", "turn_id": 7}
    assert list(reply)[:3] == ["kind", "turn_id", "source"] and list(reply)[-1] == "latency"
    assert (reply["kind"], reply["turn_id"], reply["source"]) == ("reply", 7, source)
    body = {k: v for k, v in reply.items() if k not in LINE_KEYS}
    VALIDATOR.validate(body)
    assert list(body)[0] == "say"
    latency = reply["latency"]
    assert isinstance(latency["total_ms"], int) and latency["total_ms"] >= 0
    if source == "template":
        assert latency["llm_ms"] is None
    else:
        assert isinstance(latency["llm_ms"], int) and latency["llm_ms"] >= 0


def test_unparseable_llm_text_is_a_fallback(tmp_path):
    orch = make_orch(FakeLLM(["Sure! Here you go."]), tmp_path); orch.start_session("s1")
    reply = list(orch.handle(req("s1", 1, ["jump"])))[-1]
    assert reply["source"] == "fallback" and reply["ops"] == []
    assert "reply_not_object" in log_events(tmp_path, "s1")[-1]["issues"]


def test_unexpected_adapter_exception_still_answers(tmp_path):
    orch = make_orch(FakeLLM([RuntimeError("adapter bug")]), tmp_path); orch.start_session("s1")
    reply = list(orch.handle(req("s1", 1, ["jump"])))[-1]
    assert reply["source"] == "fallback" and reply["say"]
    assert log_events(tmp_path, "s1")[-1]["error"] == "RuntimeError"


def test_hangul_flag_reaches_the_validator(tmp_path):
    script = ['{"say":"Red egg? Say: red egg!","ops":[{"op":"add","kind":"egg"}]}']
    orch = make_orch(FakeLLM(script), tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["빨간 알", "red egg"])))   # 첫 후보에 한글: 한국어 입력
    list(orch.handle(req("s1", 2, ["red egg", "빨간 알"])))   # 첫 후보가 영어
    korean, english = log_events(tmp_path, "s1")[1:3]
    assert "ops_dropped_hangul" in korean["issues"]
    assert "ops_dropped_hangul" not in english["issues"] and "ops_dropped_no_catalog" in english["issues"]


# ---------------------------------------------------------------- LLM 에 보내는 것


def test_session_prompt_fills_placeholders_and_keeps_json(tmp_path):
    llm = FakeLLM(); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    assert "{VOCAB}" not in llm.system_prompt and "{CATALOG}" not in llm.system_prompt
    assert NONE_YET in llm.system_prompt
    assert '{"say": "...", "recast": null' in llm.system_prompt  # 템플릿 속 JSON 중괄호는 그대로


def test_turn_message_carries_clean_heard_scene_and_hint(tmp_path):
    llm = FakeLLM(['{"say":"Kick!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    scene = [{"id": "ball1", "kind": "ball", "color": "red", "size": "big"}, "not an item"]
    list(orch.handle(req("s1", 1, ["uh", "  kick  the ball ", "kick a ball"], scene=scene, hint_requested=True)))
    message = llm.calls[0]
    assert "\n" not in message
    assert json.loads(message) == {
        "heard": ["kick the ball", "kick a ball"],  # 군소리만 있는 후보는 빼고 공백은 정리한다
        "scene": [scene[0]],                        # 물건(객체)이 아닌 것은 뺀다
        "applied_ops": [],
        "hint_requested": True,
    }


def test_odd_alternatives_are_ignored(tmp_path):
    llm = FakeLLM(['{"say":"Kick!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    body = req("s1", 1, [])
    body["asr"]["alternatives"] = [{"t": 5}, "jump", {"c": 0.4}, {"t": "kick"}, None]
    list(orch.handle(body))
    assert json.loads(llm.calls[0])["heard"] == ["kick"]


def test_missing_asr_is_treated_as_silence(tmp_path):
    llm = FakeLLM(["{}"]); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    reply = list(orch.handle({"session_id": "s1", "turn_id": 1}))[-1]
    assert reply["source"] == "template" and llm.calls == []


def test_only_the_first_five_alternatives_are_used(tmp_path):
    llm = FakeLLM(['{"say":"Ok!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, [f"word{i}" for i in range(9)])))
    assert json.loads(llm.calls[0])["heard"] == [f"word{i}" for i in range(5)]


def test_lone_surrogates_in_input_are_safe(tmp_path):
    llm = FakeLLM(['{"say":"Hi!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    lines = list(orch.handle(req("s1", 1, ["hi \ud800 chomp"], scene=[{"id": "x\udfff", "kind": "egg"}])))
    assert lines[1]["source"] == "llm"
    llm.calls[0].encode("utf-8")  # claude.exe 로 보낼 수 있는 글자만 남는다
    assert log_events(tmp_path, "s1")[1]["heard"] == ["hi � chomp"]


# ---------------------------------------------------------------- 개인정보


def test_redact_is_identity_for_now():
    assert redact("my name is Minji") == "my name is Minji"


def test_redact_runs_before_the_llm_and_the_log(tmp_path, monkeypatch):
    monkeypatch.setattr(turn_module, "redact", lambda text: text.replace("Minji", "[secret]"))
    llm = FakeLLM(['{"say":"Hi Minji! Nice name!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["my name is Minji"])))
    assert "Minji" not in llm.calls[0] and "[secret]" in llm.calls[0]
    raw = log_path(tmp_path, "s1").read_text(encoding="utf-8")
    assert "Minji" not in raw and "[secret]" in raw


@pytest.mark.parametrize("error, kind", [(LLMError("child said Minji at Sunny kindergarten"), "LLMError")])
def test_log_keeps_the_error_kind_not_its_message(tmp_path, error, kind):
    orch = make_orch(FakeLLM([error]), tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["jump"])))
    raw = log_path(tmp_path, "s1").read_text(encoding="utf-8")
    assert "Minji" not in raw and "Sunny" not in raw
    assert json.loads(raw.splitlines()[-1])["error"] == kind


def test_billing_refusal_prints_a_fixed_line_and_logs_the_class(tmp_path, capsys):
    refusal = LLMBillingRefused("refused: apiKeySource='ANTHROPIC_API_KEY' (child said Minji)")
    orch = make_orch(FakeLLM([refusal]), tmp_path); orch.start_session("s1")
    reply = list(orch.handle(req("s1", 1, ["jump"])))[-1]
    assert reply["source"] == "fallback" and reply["ops"] == []
    turn = log_events(tmp_path, "s1")[-1]
    assert turn["error"] == "LLMBillingRefused"
    assert "Minji" not in log_path(tmp_path, "s1").read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "LLM refused: API-key billing" in err and "Minji" not in err


def test_log_records_timeout_kind(tmp_path):
    orch = make_orch(FakeLLM(["{}"], delay_s=0.2), tmp_path, timeout_s=0.02); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["jump"])))
    turn = log_events(tmp_path, "s1")[-1]
    assert turn["error"] == "LLMTimeout" and turn["source"] == "fallback"
    assert isinstance(turn["latency"]["llm_ms"], int)


def test_successful_turn_has_no_error_field(tmp_path):
    orch = make_orch(FakeLLM(['{"say":"Hi!"}']), tmp_path); orch.start_session("s1")
    list(orch.handle(req("s1", 1, ["hello"])))
    assert "error" not in log_events(tmp_path, "s1")[-1]


# ---------------------------------------------------------------- 세션


def test_unknown_session_is_started_implicitly(tmp_path):
    llm = SpyLLM(['{"say":"Hi!"}']); orch = make_orch(llm, tmp_path)  # start_session 을 부르지 않았다
    lines = list(orch.handle(req("s9", 1, ["hello"])))
    assert lines[1]["source"] == "llm" and llm.starts == 1 and orch.session_id == "s9"
    start = log_events(tmp_path, "s9")[0]
    assert start["type"] == "session_start" and start["implicit"] is True


def test_starting_a_new_session_ends_the_previous_one(tmp_path):
    llm = SpyLLM(); orch = make_orch(llm, tmp_path)
    orch.start_session("a")
    orch.start_session("b")
    assert [e["type"] for e in log_events(tmp_path, "a")] == ["session_start", "session_end"]
    assert orch.session_id == "b" and (llm.starts, llm.ends) == (2, 1)
    assert log_events(tmp_path, "b")[0]["implicit"] is False


def test_turn_for_another_session_switches_to_it(tmp_path):
    llm = SpyLLM(['{"say":"Hi!"}']); orch = make_orch(llm, tmp_path)
    orch.start_session("a")
    list(orch.handle(req("b", 1, ["hello"])))
    assert orch.session_id == "b" and (llm.starts, llm.ends) == (2, 1)
    assert [e["type"] for e in log_events(tmp_path, "a")] == ["session_start", "session_end"]


def test_end_session_only_ends_the_current_one(tmp_path):
    llm = SpyLLM(); orch = make_orch(llm, tmp_path)
    orch.start_session("a")
    orch.end_session("other")  # 지금 세션이 아니면 아무것도 하지 않는다
    assert orch.session_id == "a" and llm.ends == 0
    orch.end_session("a")
    orch.end_session("a")  # 두 번 불러도 안전
    assert orch.session_id is None and llm.ends == 1
    assert [e["type"] for e in log_events(tmp_path, "a")] == ["session_start", "session_end"]


def test_close_ends_the_current_session(tmp_path):
    llm = SpyLLM(); orch = make_orch(llm, tmp_path)
    orch.close()  # 세션이 없어도 안전
    orch.start_session("a")
    orch.close()
    assert orch.session_id is None and llm.ends == 1


def test_llm_start_failure_does_not_stop_play(tmp_path):
    class BrokenStart(FakeLLM):
        def start_session(self, system_prompt):
            raise OSError("disk full")

    orch = make_orch(BrokenStart([LLMError("no session")]), tmp_path)
    orch.start_session("s1")
    reply = list(orch.handle(req("s1", 1, ["jump"])))[-1]
    assert reply["source"] == "fallback"
    start = log_events(tmp_path, "s1")[0]
    assert start["error"] == "OSError" and "disk" not in json.dumps(start)


def test_log_write_failure_does_not_stop_play(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the log folder should be", encoding="utf-8")
    orch = TurnOrchestrator(FakeLLM(['{"say":"Hi!"}']), SessionLog(blocker / "sessions"), TEMPLATE)
    orch.start_session("s1")
    assert list(orch.handle(req("s1", 1, ["hello"])))[-1]["say"] == "Hi!"


@pytest.mark.parametrize("bad, code", [
    (None, "bad_request"),
    ([], "bad_request"),
    ({"turn_id": 1}, "bad_session_id"),
    ({"session_id": "../evil", "turn_id": 1}, "bad_session_id"),
    ({"session_id": "nul", "turn_id": 1}, "bad_session_id"),
    ({"session_id": "s1"}, "bad_turn_id"),
    ({"session_id": "s1", "turn_id": "1"}, "bad_turn_id"),
    ({"session_id": "s1", "turn_id": True}, "bad_turn_id"),
    ({"session_id": "s1", "turn_id": -1}, "bad_turn_id"),
    ({"session_id": "s1", "turn_id": 1.5}, "bad_turn_id"),
])
def test_bad_request_raises_before_any_line(tmp_path, bad, code):
    llm = FakeLLM(['{"say":"Hi!"}']); orch = make_orch(llm, tmp_path); orch.start_session("s1")
    with pytest.raises(BadRequest) as e:
        next(orch.handle(bad))
    assert str(e.value) == code and isinstance(e.value, ValueError)
    assert llm.calls == [] and orch.session_id == "s1"


@pytest.mark.parametrize("sid", ["../x", "", None, "CON"])
def test_session_calls_reject_bad_ids(tmp_path, sid):
    orch = make_orch(FakeLLM(), tmp_path)
    for call in (orch.start_session, orch.end_session):
        with pytest.raises(BadRequest):
            call(sid)


# ---------------------------------------------------------------- SessionLog


def test_session_log_appends_lines_with_ts_first(tmp_path):
    log = SessionLog(tmp_path / "sessions")
    log.append("2026-10-22_1905", {"type": "turn", "say": "Hi!", "ts": "caller value is replaced"})
    log.append("2026-10-22_1905", {"type": "session_end"})
    lines = (tmp_path / "sessions" / "2026-10-22_1905.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert list(first) == ["ts", "type", "say"] and first["say"] == "Hi!"
    assert datetime.fromisoformat(first["ts"]).tzinfo is not None


def test_session_log_survives_lone_surrogates(tmp_path):
    log = SessionLog(tmp_path / "sessions")
    log.append("s1", {"say": "a\ud800b", "heard": ["\udfff"]})
    raw = (tmp_path / "sessions" / "s1.jsonl").read_bytes()
    assert raw.isascii()
    assert json.loads(raw)["say"] == "a\ud800b"


@pytest.mark.parametrize("sid", [
    "../x", "a/b", "a\\b", "", " ", "a.b", "nul", "CON", "com1", "Lpt9", "x" * 65, "한글", "-x", None, 3,
])
def test_session_log_rejects_unsafe_ids(tmp_path, sid):
    log = SessionLog(tmp_path / "sessions")
    with pytest.raises(ValueError):
        log.append(sid, {"type": "turn"})
    assert not list(tmp_path.rglob("*.jsonl"))


def test_session_log_accepts_the_browser_id_format(tmp_path):
    log = SessionLog(tmp_path / "sessions")
    for sid in ("2026-10-22_1905", "s1", "A", "x" * 64):
        log.append(sid, {"type": "turn"})
    assert len(list((tmp_path / "sessions").glob("*.jsonl"))) == 4


def test_session_log_is_thread_safe(tmp_path):
    log = SessionLog(tmp_path / "sessions")

    def write(n):
        for i in range(50):
            log.append("s1", {"type": "turn", "writer": n, "i": i, "say": "x" * 200})

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    lines = (tmp_path / "sessions" / "s1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200 and all(json.loads(line)["type"] == "turn" for line in lines)


# ---------------------------------------------------------------- prompt_builder


def test_build_system_prompt_fills_placeholders_and_keeps_json_braces():
    out = build_system_prompt('V={VOCAB}\nC={CATALOG}\n{"say": "..."}', vocab="Kick the ball | 공을 차",
                              catalog=NONE_YET)
    assert out == 'V=Kick the ball | 공을 차\nC=(none yet)\n{"say": "..."}'


def test_build_turn_message_spec_example():
    body = {"session_id": "2026-10-22_1905", "turn_id": 17,
            "asr": {"engine": "webspeech", "lang": "en-US",
                    "alternatives": [{"t": "kick the ball", "c": 0.71}, {"t": "kick a ball", "c": 0.52}]},
            "hint_requested": False,
            "scene": [{"id": "dino", "kind": "dino", "state": "idle"},
                      {"id": "ball1", "kind": "ball", "color": "red", "size": "big"}]}
    applied = [{"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}]
    message = build_turn_message(body, applied)
    assert "\n" not in message
    parsed = json.loads(message)
    assert list(parsed) == ["heard", "scene", "applied_ops", "hint_requested"]
    assert parsed == {"heard": ["kick the ball", "kick a ball"], "scene": body["scene"],
                      "applied_ops": applied, "hint_requested": False}


def test_build_turn_message_keeps_korean_readable_and_encodable():
    message = build_turn_message({"asr": {"alternatives": [{"t": "빨간 알"}, {"t": "x\ud800"}]}}, ())
    assert "빨간 알" in message
    message.encode("utf-8")
    assert json.loads(message)["heard"] == ["빨간 알", "x�"]


def test_build_turn_message_tolerates_missing_parts():
    assert json.loads(build_turn_message({}, ())) == {
        "heard": [], "scene": [], "applied_ops": [], "hint_requested": False}
    assert json.loads(build_turn_message({"hint_requested": "yes", "scene": "x"}, ()))["hint_requested"] is False
