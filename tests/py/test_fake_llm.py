import pytest

from server.llm import LLM, LLMError, LLMResult, LLMTimeout
from server.llm.fake import FakeLLM


def test_fake_llm_replays_script_and_records_calls():
    llm = FakeLLM(script=['{"say":"Hi!"}', '{"say":"Bye!"}'])
    llm.start_session("SYS")
    assert [llm.complete("a", 8).text, llm.complete("b", 8).text, llm.complete("c", 8).text] == [
        '{"say":"Hi!"}', '{"say":"Bye!"}', '{"say":"Bye!"}']
    assert llm.system_prompt == "SYS" and llm.calls == ["a", "b", "c"]


def test_fake_llm_raises_scripted_exception():
    llm = FakeLLM(script=[LLMError("boom")])
    llm.start_session("S")
    with pytest.raises(LLMError):
        llm.complete("x", 8)


def test_fake_llm_timeout():
    llm = FakeLLM(script=["{}"], delay_s=0.2)
    llm.start_session("S")
    with pytest.raises(LLMTimeout):
        llm.complete("x", timeout_s=0.05)


def test_llm_timeout_is_an_llm_error():
    assert issubclass(LLMTimeout, LLMError) and issubclass(LLMError, Exception)


def test_fake_llm_result_has_latency_and_default_model():
    llm = FakeLLM(script=["{}"])
    llm.start_session("S")
    r = llm.complete("x", 8)
    assert isinstance(r, LLMResult) and r.text == "{}" and isinstance(r.latency_ms, int) and r.latency_ms >= 0
    assert r.model is None


def test_fake_llm_delay_within_timeout_waits_and_succeeds():
    llm = FakeLLM(script=["ok"], delay_s=0.05)
    llm.start_session("S")
    assert llm.complete("x", timeout_s=2).text == "ok"


def test_fake_llm_default_script_returns_empty_object():
    llm = FakeLLM()
    llm.start_session("S")
    assert llm.complete("x", 8).text == "{}"


def test_fake_llm_records_timed_out_call():
    llm = FakeLLM(script=["{}"], delay_s=0.2)
    llm.start_session("S")
    with pytest.raises(LLMTimeout):
        llm.complete("x", timeout_s=0.01)
    assert llm.calls == ["x"]


def test_fake_llm_scripted_exception_repeats_after_script_ends():
    llm = FakeLLM(script=["first", LLMError("boom")])
    llm.start_session("S")
    assert llm.complete("a", 8).text == "first"
    for _ in range(2):
        with pytest.raises(LLMError):
            llm.complete("b", 8)


def test_fake_llm_system_prompt_none_until_start_and_end_session_is_safe():
    llm = FakeLLM(script=["{}"])
    assert llm.system_prompt is None
    llm.start_session("SYS")
    llm.end_session()
    llm.end_session()  # 두 번 불러도 안전


def test_fake_llm_satisfies_protocol():
    llm: LLM = FakeLLM()
    assert hasattr(llm, "start_session") and hasattr(llm, "complete") and hasattr(llm, "end_session")


def test_fake_llm_counts_concurrent_calls():
    # 턴 직렬화 테스트가 쓰는 계기: 동시에 complete 안에 있던 호출 수의 최댓값.
    import threading

    llm = FakeLLM(script=["{}"], delay_s=0.1)
    llm.start_session("S")
    assert llm.max_concurrent == 0
    go = threading.Barrier(2)

    def call():
        go.wait()
        llm.complete("x", timeout_s=2)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert llm.max_concurrent == 2  # 막는 것이 없으면 겹친다
    llm.complete("y", timeout_s=2)
    assert llm.max_concurrent == 2


def test_fake_llm_counter_recovers_after_errors_and_timeouts():
    llm = FakeLLM(script=[LLMError("boom")], delay_s=0.0)
    with pytest.raises(LLMError):
        llm.complete("a", 1)
    slow = FakeLLM(script=["{}"], delay_s=0.2)
    with pytest.raises(LLMTimeout):
        slow.complete("a", timeout_s=0.01)
    assert llm.max_concurrent == 1 and slow.max_concurrent == 1
    assert llm._active == 0 and slow._active == 0
