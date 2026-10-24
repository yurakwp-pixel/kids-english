"""LLM 지연·격리 측정: `.venv/Scripts/python scripts/bench_llm.py --turns 20`

ClaudeAgentLLM 세션 하나를 열고 고정 입력을 차례로 보낸다(서버와 같은 환경 정리, 같은 옵션).
출력: 환경에서 지운 변수 이름, 쓴 옵션, 턴별 지연(ms), 격리 점검표(init_info), p50/p95.
종료 코드: 0 = 정상, 1 = 연결·턴 오류, 2 = 격리 실패(첫 턴에서 판정하고 바로 멈춘다. 쿼터를 더 쓰지 않는다).

실제 Claude 를 부르므로 구독 쿼터를 조금 쓴다(haiku, 턴 수만큼). 비밀 값은 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python scripts/bench_llm.py` 로 실행해도 server 패키지를 찾게 한다
    sys.path.insert(0, str(ROOT))

from server.config import load_config, scrub_llm_env  # noqa: E402
from server.llm import LLMError  # noqa: E402
from server.llm.claude_agent import (  # noqa: E402
    SYSTEM_PROMPT_FILE,
    ClaudeAgentLLM,
    build_options,
    check_isolation,
    default_workdir,
    isolation_ok,
)

PROMPT_PATH = ROOT / "prompts" / "chomp_system.md"
LATENCY_TARGET_MS = {"p50": 2500, "p95": 4000}  # spec G1
MAX_ERROR_STREAK = 3  # 같은 오류(예: 로그인 안 됨)로 턴을 계속 버리지 않는다

# 아이가 실제로 할 법한 말 20개: 인사, 동작, 꾸미는 말 조합, 한국어, 놀이 밖 질문, 틀린 문장.
INPUTS: list[str] = [
    "hello chomp", "jump", "kick the ball", "빨간 알", "what is your name",
    "turn around", "big red egg", "kick ball", "bark", "thumbs up",
    "i like dinosaurs", "공 차", "two blue balls", "stretch your arms", "where is the egg",
    "spin", "yawn", "let's dance", "give me five", "good night",
]


def bench_workdir() -> Path:
    """서버(`default_workdir()`)와 다른 빈 폴더. 서버가 떠 있을 때 bench 를 돌려도 서버 세션의 system_prompt.md 를
    바꿔치기하지 않는다. 서버 폴더와 같은 곳(ASCII 임시 폴더)에 이름만 다르게 둔다."""
    return default_workdir().parent / "kids_english_llm_bench"


def fill_prompt(template: str) -> str:
    """M1: 단어장과 카탈로그가 아직 없으니 자리표시자를 "(none yet)" 으로 채운다(R1, Task 6 전까지)."""
    return template.replace("{VOCAB}", "(none yet)").replace("{CATALOG}", "(none yet)")


def turn_message(text: str) -> str:
    """Task 6 build_turn_message 와 같은 모양의 한 줄 JSON(인식 후보 하나, 빈 장면)."""
    return json.dumps({"heard": [text], "scene": [], "applied_ops": [], "hint_requested": False},
                      ensure_ascii=False)


def percentile(values: list[float], p: float) -> float:
    """선형 보간 백분위수(statistics.quantiles 의 inclusive 방식과 같다)."""
    if not values:
        raise ValueError("percentile of empty data")
    data = sorted(values)
    k = (len(data) - 1) * p / 100
    lo = math.floor(k)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def _one_line(text: str, width: int = 100) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def _is_json(text: str) -> bool:
    try:
        return isinstance(json.loads(text.strip()), dict)
    except ValueError:
        return False


def _print_options(llm: ClaudeAgentLLM, token_given: bool) -> None:
    o = build_options(llm.model, llm.workdir / SYSTEM_PROMPT_FILE, llm.workdir, None)
    print("options:")
    for name in ("model", "system_prompt", "tools", "allowed_tools", "disallowed_tools", "mcp_servers",
                 "strict_mcp_config", "setting_sources", "permission_mode", "max_turns", "thinking",
                 "effort", "extra_args", "cwd"):
        print(f"  {name:<18} {getattr(o, name)!r}")
    print(f"  {'env':<18} {o.env!r}")
    print(f"  {'oauth token':<18} {'given via .env/environment' if token_given else 'not given (logged-in subscription)'}")


def _print_isolation(init_info: dict | None) -> bool:
    checks = check_isolation(init_info)
    print("isolation (init_info):")
    print(f"  {'key':<14} {'result':<6} value")
    for c in checks:
        print(f"  {c.key:<14} {'OK' if c.ok else 'FAIL':<6} {c.shown}")
    if init_info is not None:
        print("  info (not judged):")
        for key in ("apiKeySource", "permissionMode", "cwd", "claude_code_version", "skills",
                    "slash_commands", "agents"):
            if key in init_info:
                value = init_info[key]
                shown = f"{len(value)}: {', '.join(map(str, value[:8]))}" if isinstance(value, list) else value
                print(f"    {key:<20} {shown}")
    return isolation_ok(checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClaudeAgentLLM latency and isolation bench")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per turn (default 30)")
    parser.add_argument("--model", default="haiku")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # 콘솔 인코딩에 없는 글자가 와도 멈추지 않게

    removed = scrub_llm_env(os.environ)  # run.py 와 똑같이
    print(f"env removed (names only): {', '.join(sorted(removed)) or '(none)'}")
    cfg = load_config(ROOT, [])
    llm = ClaudeAgentLLM(args.model, workdir=bench_workdir(), oauth_token=cfg.oauth_token)
    _print_options(llm, cfg.oauth_token is not None)

    started = time.perf_counter()
    llm.start_session(fill_prompt(PROMPT_PATH.read_text(encoding="utf-8")))
    print(f"start_session returned in {int((time.perf_counter() - started) * 1000)} ms")
    if not llm.wait_ready(90):
        print("could not connect to claude:", file=sys.stderr)
        for line in llm.stderr_tail[-15:]:
            print(f"  stderr: {line}", file=sys.stderr)
        llm.end_session()
        return 1
    print(f"connected in {llm.connect_ms} ms (claude.exe start + initialize)")

    latencies: list[int] = []
    errors = 0
    json_ok = 0
    streak = 0  # 연달아 난 오류 수
    checked = False  # 첫 성공 턴 뒤에 격리를 판정한다(init 은 턴이 시작될 때 온다)
    isolated = False
    print("turns:")
    for i in range(args.turns):
        said = INPUTS[i % len(INPUTS)]
        try:
            r = llm.complete(turn_message(said), timeout_s=args.timeout)
        except LLMError as e:
            errors += 1
            streak += 1
            print(f"  {i + 1:>2}  ERROR  {said!r}: {type(e).__name__}: {e}")
            if streak >= MAX_ERROR_STREAK:
                print(f"{streak} errors in a row: stopping.")
                break
            continue
        streak = 0
        latencies.append(r.latency_ms)
        ok = _is_json(r.text)
        json_ok += ok
        print(f"  {i + 1:>2} {r.latency_ms:>6} ms  json={'ok' if ok else 'NO'}  {said!r:<22} -> {_one_line(r.text)}")
        if not checked:
            checked = True
            isolated = _print_isolation(llm.init_info)
            if not isolated:
                print("ISOLATION FAILED: stopping before spending more turns.")
                break

    end_started = time.perf_counter()
    llm.end_session()
    print(f"end_session took {int((time.perf_counter() - end_started) * 1000)} ms")
    if not checked:
        if llm.init_info is not None:  # 턴은 실패했어도 init 은 왔다: 참고용으로 보여 준다
            _print_isolation(llm.init_info)
        print("no successful turn, isolation not judged", file=sys.stderr)
        for line in llm.stderr_tail[-15:]:
            print(f"  stderr: {line}", file=sys.stderr)
        return 1
    if not isolated:
        return 2
    if latencies:
        p50, p95 = percentile(latencies, 50), percentile(latencies, 95)
        print(f"latency ms over {len(latencies)} turns: p50={p50:.0f} p95={p95:.0f} "
              f"min={min(latencies)} max={max(latencies)} mean={sum(latencies) / len(latencies):.0f} "
              f"(target p50<={LATENCY_TARGET_MS['p50']} p95<={LATENCY_TARGET_MS['p95']}: "
              f"{'met' if p50 <= LATENCY_TARGET_MS['p50'] and p95 <= LATENCY_TARGET_MS['p95'] else 'missed'})")
        print(f"replies that are one plain JSON object: {json_ok}/{len(latencies)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
