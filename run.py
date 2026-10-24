"""진입점: python run.py [--host H] [--port P] [--fake-llm]"""
import os
import sys
from pathlib import Path

from server.app import make_server
from server.config import load_config, scrub_llm_env

ROOT = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    # 구독 인증만 쓰고, 아이용 claude.exe 가 부모 Claude Code 세션의 변수를 물려받지 않게 한다(이름만 출력).
    removed = scrub_llm_env(os.environ)
    if removed:
        print(f"Removed from the environment (subscription auth only): {', '.join(sorted(removed))}",
              file=sys.stderr)
    cfg = load_config(ROOT, argv)
    server = make_server(cfg, None)
    print(f"http://{cfg.host}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
