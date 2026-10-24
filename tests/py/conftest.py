import shutil
import threading
from pathlib import Path

import pytest

from server.app import make_server
from server.config import load_config

REAL_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project_root(tmp_path):
    """임시 프로젝트 루트: 진짜 web/, prompts/, content/ 사본과 shared/catalog.json + 비어 있는 docs/
    + 노출되면 안 되는 .env, run.py.

    테스트가 진짜 docs/, data/ 폴더에 파일을 남기지 않도록 모든 서버 테스트는 이 루트에서 돈다.
    run.py 의 build_orchestrator 가 카탈로그와 단어장을 이 루트에서 읽는다.
    """
    shutil.copytree(REAL_ROOT / "web", tmp_path / "web")
    shutil.copytree(REAL_ROOT / "prompts", tmp_path / "prompts")
    shutil.copytree(REAL_ROOT / "content", tmp_path / "content")
    (tmp_path / "docs").mkdir()
    (tmp_path / "shared").mkdir()
    shutil.copy(REAL_ROOT / "shared" / "catalog.json", tmp_path / "shared" / "catalog.json")
    (tmp_path / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=do-not-serve\n", encoding="utf-8")
    (tmp_path / "run.py").write_text("print('do-not-serve')\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def make_base_url(project_root):
    """서버를 0번 포트(빈 포트 자동 배정)로 스레드에서 띄우는 팩토리. 테스트가 끝나면 모두 내린다.

    orchestrator_factory(cfg) 를 주면 그 오케스트레이터를 단다(없으면 M0 처럼 None: /api/turn 은 503).
    """
    servers = []

    def _make(argv, orchestrator_factory=None):
        cfg = load_config(project_root, ["--port", "0", *argv], environ={})
        orchestrator = orchestrator_factory(cfg) if orchestrator_factory else None
        server = make_server(cfg, orchestrator)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append((server, orchestrator))
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    yield _make
    for server, orchestrator in servers:
        server.shutdown()
        server.server_close()
        if orchestrator is not None:
            orchestrator.close()


@pytest.fixture
def base_url(make_base_url):
    return make_base_url(["--fake-llm"])
