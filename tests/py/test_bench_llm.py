"""scripts/bench_llm.py 의 순수 함수만 본다(쿼터 사용 0)."""
import json

import pytest

from scripts.bench_llm import INPUTS, fill_prompt, percentile, turn_message


def test_percentile_interpolates_like_inclusive_quantiles():
    data = [100, 200, 300, 400, 500]
    assert percentile(data, 50) == 300
    assert percentile(data, 95) == pytest.approx(480)
    assert percentile([7], 95) == 7
    assert percentile([300, 100, 200], 0) == 100 and percentile([300, 100, 200], 100) == 300


def test_percentile_rejects_empty_data():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_fixed_inputs_cover_the_brief_examples():
    assert len(INPUTS) == 20
    for must in ["hello chomp", "jump", "kick the ball", "빨간 알", "what is your name", "turn around"]:
        assert must in INPUTS


def test_fill_prompt_replaces_both_placeholders():
    assert fill_prompt("V={VOCAB} C={CATALOG}") == "V=(none yet) C=(none yet)"


def test_turn_message_matches_the_task6_shape():
    msg = json.loads(turn_message("빨간 알"))
    assert msg == {"heard": ["빨간 알"], "scene": [], "applied_ops": [], "hint_requested": False}


def test_bench_uses_its_own_workdir():
    # 서버가 쓰는 작업 폴더의 system_prompt.md 를 bench 가 덮어쓰지 않게 폴더를 나눈다.
    from scripts.bench_llm import bench_workdir
    from server.llm.claude_agent import default_workdir

    path = bench_workdir()
    assert path != default_workdir() and path.parent == default_workdir().parent
    assert path.name == "kids_english_llm_bench" and str(path).isascii()
