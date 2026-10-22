"""진입점: python run.py [--host H] [--port P] [--fake-llm]"""
import os
import sys
from pathlib import Path

from server.app import make_server
from server.config import load_config, scrub_api_key_env

ROOT = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    # 구독 인증만 쓴다: 환경에 API 키가 있으면 지워서 API 요금이 실수로 나가지 않게 한다.
    if scrub_api_key_env(os.environ):
        print("Removed ANTHROPIC_API_KEY from the environment (subscription auth only).", file=sys.stderr)
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
