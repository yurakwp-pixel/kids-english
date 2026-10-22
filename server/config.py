"""설정: 명령줄 플래그, 환경변수, 프로젝트 루트의 .env 를 하나의 Config 로 모은다.

우선순위(높은 쪽이 이긴다): 명령줄 플래그 > 실제 환경변수 > .env 파일 > 기본값.
토큰 같은 비밀 값은 코드·로그·커밋에 남기지 않는다(Config 의 repr 에서도 뺀다).
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, MutableMapping

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771
OAUTH_TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_KEY = "ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class Config:
    root: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    model: str = "haiku"
    llm_timeout_s: float = 8.0
    use_fake_llm: bool = False
    # repr=False: 실수로 cfg 를 출력해도 토큰이 로그에 남지 않게 한다.
    oauth_token: str | None = field(default=None, repr=False)


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=VALUE 줄만 읽는다. 빈 줄, `#` 주석, `=` 없는 줄은 무시하고 값을 감싼 따옴표 한 쌍은 벗긴다."""
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def scrub_api_key_env(environ: MutableMapping[str, str]) -> bool:
    """환경에서 ANTHROPIC_API_KEY 를 지워 API 요금이 실수로 나가지 않게 한다. 지웠으면 True."""
    if API_KEY_KEY in environ:
        del environ[API_KEY_KEY]
        return True
    return False


def _read_dotenv(root: Path) -> dict[str, str]:
    path = root / ".env"
    if not path.is_file():
        return {}
    # utf-8-sig: 메모장이 붙이는 BOM 때문에 첫 줄 키가 깨지지 않게 한다.
    return parse_dotenv(path.read_text(encoding="utf-8-sig"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="kids-english 로컬 서버")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"바인딩 주소 (기본 {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"포트 (기본 {DEFAULT_PORT})")
    parser.add_argument("--fake-llm", action="store_true", help="Claude 대신 FakeLLM 을 쓴다")
    return parser


def load_config(
    root: Path,
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """root/.env 와 environ 과 argv 를 합쳐 Config 를 만든다. argv 가 None 이면 플래그가 없는 것으로 본다."""
    args = _build_parser().parse_args([] if argv is None else argv)
    merged = {**_read_dotenv(root), **(os.environ if environ is None else environ)}
    return Config(
        root=root,
        host=args.host,
        port=args.port,
        use_fake_llm=args.fake_llm,
        oauth_token=merged.get(OAUTH_TOKEN_KEY) or None,
    )
