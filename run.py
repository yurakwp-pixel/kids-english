"""진입점: python run.py [--host H] [--port P] [--fake-llm]

`--fake-llm` 이면 돈도 네트워크도 쓰지 않는 FakeLLM 이 준비된 영어 한마디로 답한다(화면 개발용).
아니면 이 PC 의 Claude Code 구독으로 ClaudeAgentLLM(haiku)을 쓴다.
어느 쪽이든 `shared/catalog.json`(그릴 수 있는 것)과 `content/words/`(배울 단어)를 읽어 fastpath·부화·검증기에 쓴다.
"""
import json
import os
import sys
from pathlib import Path

from server.app import make_server
from server.canned import fallback_reply, praise
from server.catalog import load_catalog
from server.config import Config, load_config, scrub_llm_env
from server.llm.claude_agent import ClaudeAgentLLM
from server.llm.fake import FakeLLM
from server.session_log import SessionLog
from server.turn import TurnOrchestrator
from server.words import load_words

ROOT = Path(__file__).resolve().parent
PROMPT_FILE = Path("prompts") / "chomp_system.md"
CATALOG_FILE = Path("shared") / "catalog.json"
WORDS_DIR = Path("content") / "words"
SESSIONS_DIR = Path("data") / "sessions"


def fake_llm_script() -> list[str]:
    """FakeLLM 이 차례로 내는 답(다 쓰면 마지막 것을 되풀이한다). 칭찬 다섯 가지로 만든 영어 한 줄 JSON 이다.

    모양은 `fallback_reply` 를 빌려 스키마를 그대로 만족하고, 대사만 놀이 시작 인사로 바꾼다.
    """
    lines = []
    for i in range(5):
        reply = fallback_reply("unclear", i)
        reply.update(
            say=f"{praise(i)} I'm Chomp. Let's play!",
            korean_hint="같이 놀자! → Let's play!",
            utterance={"lang": "en", "words_used": [], "errors": []},
        )
        lines.append(json.dumps(reply, ensure_ascii=False))
    return lines


def build_orchestrator(cfg: Config) -> TurnOrchestrator:
    """카탈로그와 단어장을 읽어(틀렸으면 어느 파일의 무엇인지 담은 ValueError 로 서버가 뜨지 않는다) 턴 처리기를 만든다.

    `--fake-llm` 이어도 fastpath·부화·검증기는 진짜와 똑같이 돈다. 바뀌는 것은 LLM 의 답뿐이다.
    """
    if cfg.use_fake_llm:
        llm = FakeLLM(fake_llm_script())
    else:  # 작업 폴더는 기본값(default_workdir). bench_llm.py 는 다른 폴더를 쓴다.
        llm = ClaudeAgentLLM(cfg.model, oauth_token=cfg.oauth_token)
    template = (cfg.root / PROMPT_FILE).read_text(encoding="utf-8")
    catalog = load_catalog(cfg.root / CATALOG_FILE)
    bank = load_words(cfg.root / WORDS_DIR, catalog)
    return TurnOrchestrator(llm, SessionLog(cfg.root / SESSIONS_DIR), template, bank=bank, catalog=catalog,
                            timeout_s=cfg.llm_timeout_s)


def main(argv: list[str]) -> int:
    # 구독 인증만 쓰고, 아이용 claude.exe 가 부모 Claude Code 세션의 변수를 물려받지 않게 한다(이름만 출력).
    removed = scrub_llm_env(os.environ)
    if removed:
        print(f"Removed from the environment (subscription auth only): {', '.join(sorted(removed))}",
              file=sys.stderr)
    cfg = load_config(ROOT, argv)
    orchestrator = build_orchestrator(cfg)
    server = make_server(cfg, orchestrator)
    host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
    print(f"http://{host}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        orchestrator.close()  # 열린 세션을 끝내고 claude.exe 를 닫는다
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
