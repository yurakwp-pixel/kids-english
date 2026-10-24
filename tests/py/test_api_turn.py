"""HTTP: /api/session/start, /api/turn(NDJSON 두 줄), /api/session/end. 서버는 FakeLLM 으로 뜬다(쿼터 0)."""
import http.client
import json
import socket
import struct
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

import run
from run import build_orchestrator, fake_llm_script
from server.app import AppServer, ndjson_line
from server.config import load_config
from server.llm.claude_agent import ClaudeAgentLLM, default_workdir
from server.llm.fake import FakeLLM
from server.reply import validate_reply
from server.session_log import SessionLog
from server.text import has_hangul
from server.turn import TurnOrchestrator

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "prompts" / "chomp_system.md").read_text(encoding="utf-8")


def turn_req(session_id, turn_id, alts):
    return {"session_id": session_id, "turn_id": turn_id,
            "asr": {"engine": "webspeech", "lang": "en-US", "alternatives": [{"t": t, "c": 0.8} for t in alts]},
            "hint_requested": False, "scene": []}


def post(url, body=None, *, raw=None):
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=10) as res:
            return res.status, res.headers, res.read()
    except HTTPError as e:
        with e:  # 오류 응답도 소켓을 닫는다(ResourceWarning 없이)
            return e.code, e.headers, e.read()


def events(project_root, session_id):
    path = project_root / "data" / "sessions" / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def turn_url(make_base_url):
    """run.py 와 같은 방법으로 만든 오케스트레이터(--fake-llm)를 단 서버."""
    return make_base_url(["--fake-llm"], orchestrator_factory=build_orchestrator)


# ---------------------------------------------------------------- brief


HATCH = {"op": "act", "actor": "dino", "action": "hatch"}


def test_turn_streams_exactly_two_ndjson_lines(turn_url):
    status, _, body = post(turn_url + "/api/session/start", {"session_id": "2026-10-22_1905"})
    assert status == 200 and json.loads(body) == {"ok": True}
    status, headers, body = post(turn_url + "/api/turn", turn_req("2026-10-22_1905", 1, ["i like you"]))
    assert status == 200 and headers["Content-Type"] == "application/x-ndjson"
    assert body.endswith(b"\n") and body.isascii()
    lines = body.decode("ascii").splitlines()
    assert len(lines) == 2
    first, reply = (json.loads(line) for line in lines)
    # 세션의 첫 영어: fastpath 에 없는 말이어도 1줄에 부화가 실린다(M2)
    assert first == {"kind": "fast", "turn_id": 1, "ops": [HATCH], "sfx": "pop"}
    assert reply["kind"] == "reply" and reply["turn_id"] == 1 and reply["source"] == "llm"
    assert reply["say"] == json.loads(fake_llm_script()[0])["say"]
    _, _, body = post(turn_url + "/api/turn", turn_req("2026-10-22_1905", 2, ["i like you"]))
    assert json.loads(body.splitlines()[0]) == {"kind": "ack", "turn_id": 2}  # 부화는 한 번만


def test_known_words_take_the_fastpath_without_the_llm(turn_url, project_root):
    lines = [json.loads(line) for line in post(turn_url + "/api/turn", turn_req("s1", 1, ["jump"]))[2].splitlines()]
    assert lines[0] == {"kind": "fast", "turn_id": 1, "sfx": "pop",
                        "ops": [HATCH, {"op": "act", "actor": "chomp", "action": "jump"}]}
    assert lines[1]["source"] == "template" and lines[1]["korean_hint"] == "뛰어 → Jump!"
    assert lines[1]["latency"]["llm_ms"] is None and lines[1]["ops"] == []


def test_ack_is_flushed_before_the_llm_answers(make_base_url):
    def slow(cfg):
        llm = FakeLLM(['{"say":"Slow hi!"}'], delay_s=0.6)
        return TurnOrchestrator(llm, SessionLog(cfg.root / "data" / "sessions"), TEMPLATE)

    parts = urlsplit(make_base_url([], orchestrator_factory=slow))
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        started = time.perf_counter()
        conn.request("POST", "/api/turn", body=json.dumps(turn_req("s1", 1, ["hello"])),
                     headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        first = res.readline()
        first_at = time.perf_counter() - started
        second = res.readline()
        second_at = time.perf_counter() - started
    finally:
        conn.close()
    assert json.loads(first)["kind"] == "ack" and json.loads(second)["kind"] == "reply"
    assert second_at >= 0.6 and first_at < second_at - 0.3  # 첫 줄은 LLM 을 기다리지 않고 나간다


def test_a_client_that_leaves_mid_turn_does_not_keep_the_turn_lock(make_base_url):
    # 아이 화면이 1줄만 읽고 연결을 끊었다(새로고침, 탭 닫기). 서버의 그 턴이 끝나면 잠금이 풀려서 다음 턴이 두 줄로 나가야 한다.
    made = {}

    def slow(cfg):
        llm = FakeLLM(['{"say":"Slow hi!"}'], delay_s=0.3)
        made["orch"] = TurnOrchestrator(llm, SessionLog(cfg.root / "data" / "sessions"), TEMPLATE)
        made["llm"] = llm
        return made["orch"]

    url = make_base_url([], orchestrator_factory=slow)
    parts = urlsplit(url)
    body = json.dumps(turn_req("s1", 1, ["hello"])).encode("utf-8")
    with socket.create_connection((parts.hostname, parts.port), timeout=5) as sock:
        sock.sendall(b"POST /api/turn HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\n"
                     b"Content-Length: %d\r\n\r\n" % len(body) + body)
        reader = sock.makefile("rb")
        assert reader.readline().split()[1] == b"200"
        while reader.readline() not in (b"\r\n", b""):  # 헤더
            pass
        first = json.loads(reader.readline())
        reader.close()
        # LLM 이 답하는 중에 끊는다. linger 0: 닫을 때 RST 를 보내서 서버의 reply 쓰기가 실패하게 한다
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    assert first == {"kind": "ack", "turn_id": 1}

    orch, llm = made["orch"], made["llm"]
    deadline = time.monotonic() + 5
    while (not llm.calls or orch._lock.locked()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert llm.calls and not orch._lock.locked()  # 끊긴 턴은 끝났고 잠금은 풀렸다

    status, _, raw = post(url + "/api/turn", turn_req("s1", 2, ["hello again"]))
    lines = [json.loads(line) for line in raw.splitlines()]
    assert status == 200 and [(line["kind"], line["turn_id"]) for line in lines] == [("ack", 2), ("reply", 2)]
    assert lines[1]["say"] == "Slow hi!" and len(llm.calls) == 2
    assert not orch._lock.locked()


# ---------------------------------------------------------------- 세션과 로그


def test_session_end_and_log(turn_url, project_root):
    assert post(turn_url + "/api/session/start", {"session_id": "s1"})[0] == 200
    assert post(turn_url + "/api/turn", turn_req("s1", 1, ["jump"]))[0] == 200
    status, _, body = post(turn_url + "/api/session/end", {"session_id": "s1"})
    assert status == 200 and json.loads(body) == {"ok": True}
    assert [e["type"] for e in events(project_root, "s1")] == ["session_start", "turn", "session_end"]


def test_unknown_session_is_started_implicitly(turn_url, project_root):
    # 서버가 다시 켜진 뒤에도 열려 있던 아이 화면의 다음 턴이 그대로 이어진다(400 대신 세션을 연다).
    status, _, body = post(turn_url + "/api/turn", turn_req("s-new", 1, ["hello"]))
    assert status == 200 and len(body.splitlines()) == 2
    first = events(project_root, "s-new")[0]
    assert first["type"] == "session_start" and first["implicit"] is True


def test_empty_recognition_gets_a_template_reply(turn_url):
    status, _, body = post(turn_url + "/api/turn", turn_req("s1", 2, []))
    reply = json.loads(body.splitlines()[1])
    assert status == 200 and reply["source"] == "template" and reply["latency"]["llm_ms"] is None


# ---------------------------------------------------------------- 잘못된 요청


@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b'"text"', b"", b"\xff\xfe\x00junk"])
@pytest.mark.parametrize("path", ["/api/turn", "/api/session/start", "/api/session/end"])
def test_bad_json_is_400(turn_url, path, raw):
    status, headers, body = post(turn_url + path, raw=raw)
    assert status == 400 and headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"ok": False, "error": "bad_json"}


@pytest.mark.parametrize("path, body, code", [
    ("/api/turn", {"session_id": "../x", "turn_id": 1}, "bad_session_id"),
    ("/api/turn", {"session_id": "s1", "turn_id": "one"}, "bad_turn_id"),
    ("/api/session/start", {"session_id": "nul"}, "bad_session_id"),
    ("/api/session/start", {}, "bad_session_id"),
    ("/api/session/end", {"session_id": "a/b"}, "bad_session_id"),
])
def test_bad_fields_are_400(turn_url, project_root, path, body, code):
    status, _, raw = post(turn_url + path, body)
    assert status == 400 and json.loads(raw) == {"ok": False, "error": code}
    assert not (project_root / "data").exists() or not list((project_root / "data").rglob("*x*"))


def test_body_too_large_is_413(turn_url):
    status, _, body = post(turn_url + "/api/turn", raw=b'{"pad": "' + b"a" * (70 * 1024) + b'"}')
    assert status == 413 and json.loads(body) == {"ok": False, "error": "too_large"}


def test_missing_content_length_is_411(turn_url):
    parts = urlsplit(turn_url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        conn.putrequest("POST", "/api/turn")
        conn.endheaders()
        res = conn.getresponse()
        assert res.status == 411 and json.loads(res.read()) == {"ok": False, "error": "length_required"}
    finally:
        conn.close()


def test_lone_surrogate_in_body_is_safe(turn_url):
    raw = b'{"session_id":"s1","turn_id":1,"asr":{"alternatives":[{"t":"hi \\ud800 chomp"}]}}'
    status, _, body = post(turn_url + "/api/turn", raw=raw)
    assert status == 200 and body.isascii() and len(body.splitlines()) == 2


def test_post_without_orchestrator_is_503(base_url):
    status, _, body = post(base_url + "/api/turn", turn_req("s1", 1, ["hi"]))
    assert status == 503 and json.loads(body) == {"ok": False, "error": "no_orchestrator"}


def test_unknown_post_route_is_404(turn_url):
    assert post(turn_url + "/api/nope", {})[0] == 404
    assert post(turn_url + "/js/ndjson.js", {})[0] == 404


def test_get_on_turn_is_404(turn_url):
    with pytest.raises(HTTPError) as e:
        urlopen(turn_url + "/api/turn")
    with e.value:
        assert e.value.code == 404


# ---------------------------------------------------------------- NDJSON 한 줄


def test_ndjson_line_is_one_ascii_line_even_with_lone_surrogates():
    line = ndjson_line({"kind": "reply", "say": "Hi \ud800!", "korean_hint": "공 → Ball!"})
    assert line.endswith(b"\n") and line.count(b"\n") == 1 and line.isascii()
    assert json.loads(line) == {"kind": "reply", "say": "Hi \ud800!", "korean_hint": "공 → Ball!"}


# ---------------------------------------------------------------- run.py 연결


def test_fake_llm_script_is_friendly_english_the_validator_keeps():
    script = fake_llm_script()
    assert len(script) >= 3
    for i, text in enumerate(script):
        assert "\n" not in text
        obj = json.loads(text)
        reply, issues = validate_reply(obj, turn_id=i)
        assert issues == [] and reply == obj
        assert reply["say"] and not has_hangul(reply["say"]) and len(reply["say"].split()) <= 15
    assert len({json.loads(text)["say"] for text in script}) == len(script)


def test_build_orchestrator_picks_the_llm(project_root):
    fake = build_orchestrator(load_config(project_root, ["--fake-llm"], environ={}))
    assert isinstance(fake.llm, FakeLLM) and fake.timeout_s == 8.0
    real = build_orchestrator(load_config(project_root, [], environ={}))
    # 진짜 세션은 열지 않는다(쿼터 0). 서버는 기본 작업 폴더를 쓴다(bench 와 다른 폴더).
    assert isinstance(real.llm, ClaudeAgentLLM) and real.llm.workdir == default_workdir()
    assert real.llm.model == "haiku"


def test_build_orchestrator_loads_the_words_and_the_catalog(project_root):
    orch = build_orchestrator(load_config(project_root, ["--fake-llm"], environ={}))
    assert orch.catalog is not None and "hatch" in orch.catalog.actor_actions("dino")
    assert orch.bank is not None and orch.level == 2
    prompt = orch.system_prompt()
    assert "Kick the ball | 공을 차" in prompt and "egg: color, size" in prompt
    assert "{VOCAB}" not in prompt and "{CATALOG}" not in prompt


def test_main_serves_and_closes(project_root, monkeypatch, capsys):
    def stop_at_once(self, *args, **kwargs):
        raise KeyboardInterrupt

    closed = []
    monkeypatch.setattr(run, "ROOT", project_root)
    monkeypatch.setattr(run, "scrub_llm_env", lambda environ: [])  # 테스트 프로세스의 환경은 건드리지 않는다
    monkeypatch.setattr(AppServer, "serve_forever", stop_at_once)
    monkeypatch.setattr(TurnOrchestrator, "close", lambda self: closed.append(self))
    assert run.main(["--fake-llm", "--port", "0"]) == 0
    assert capsys.readouterr().out.startswith("http://127.0.0.1:")
    assert len(closed) == 1
