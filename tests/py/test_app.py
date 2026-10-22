import json
import socket
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import pytest

from server.app import make_server, resolve_static
from server.config import load_config


def get_json(url):
    return json.loads(urlopen(url).read())


def test_health(base_url):
    assert get_json(base_url + "/api/health") == {"ok": True, "llm": "fake", "model": "haiku"}


def test_health_reports_claude_when_not_fake(make_base_url):
    assert get_json(make_base_url([]) + "/api/health") == {"ok": True, "llm": "claude", "model": "haiku"}


def test_index_served(base_url):
    res = urlopen(base_url + "/")
    assert b"<html" in res.read()
    assert res.headers["Content-Type"] == "text/html; charset=utf-8"


@pytest.mark.parametrize("path, content_type", [
    ("/js/ndjson.js", "text/javascript; charset=utf-8"),
    ("/css/app.css", "text/css; charset=utf-8"),
])
def test_static_files_have_utf8_content_types(base_url, path, content_type):
    assert urlopen(base_url + path).headers["Content-Type"] == content_type


@pytest.mark.parametrize("p", [
    "/docs/../.env",
    "/%2e%2e/.env",
    "/shared/..%2f..%2frun.py",
    "/js/../../run.py",
    "/js/..%5c..%5crun.py",
    "/docs/%2e%2e/%2e%2e/run.py",
    "/js/..%00/index.html",
    "/js//run.py",
    "/js/C:/Windows/win.ini",
])
def test_traversal_is_404(base_url, p):
    with pytest.raises(HTTPError) as e:
        urlopen(base_url + p)
    assert e.value.code == 404


@pytest.mark.parametrize("p", ["/.env", "/run.py", "/server/app.py", "/data/x.jsonl", "/web/index.html", "/nope"])
def test_unmapped_paths_are_404(base_url, p):
    with pytest.raises(HTTPError) as e:
        urlopen(base_url + p)
    assert e.value.code == 404


def test_unknown_api_is_404(base_url):
    with pytest.raises(HTTPError) as e:
        urlopen(base_url + "/api/nope")
    assert e.value.code == 404


def test_query_string_is_ignored(base_url):
    assert urlopen(base_url + "/css/app.css?v=3").status == 200


def test_korean_filename_served(base_url, project_root):
    (project_root / "docs" / "한글 테스트.md").write_text("# 안녕", encoding="utf-8")
    res = urlopen(base_url + "/docs/" + quote("한글 테스트.md"))
    assert res.status == 200
    assert res.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert res.read().decode("utf-8") == "# 안녕"


def test_percent_encoding_is_decoded_only_once(base_url, project_root):
    (project_root / "docs" / "a%20b.md").write_text("literal", encoding="utf-8")
    assert urlopen(base_url + "/docs/a%2520b.md").read() == b"literal"
    with pytest.raises(HTTPError) as e:
        urlopen(base_url + "/docs/a%252520b.md")
    assert e.value.code == 404


def test_docs_directory_serves_its_index(base_url, project_root):
    (project_root / "docs" / "index.html").write_text("<html>docs viewer</html>", encoding="utf-8")
    assert b"docs viewer" in urlopen(base_url + "/docs/").read()


def test_shared_files_served(base_url, project_root):
    (project_root / "shared" / "catalog.json").write_text('{"a": 1}', encoding="utf-8")
    assert get_json(base_url + "/shared/catalog.json") == {"a": 1}


def test_resolve_static_maps_prefixes_to_folders(project_root):
    assert resolve_static("/", project_root) == (project_root / "web" / "index.html").resolve()
    assert resolve_static("/js/ndjson.js", project_root) == (project_root / "web" / "js" / "ndjson.js").resolve()
    assert resolve_static("/css/app.css", project_root) == (project_root / "web" / "css" / "app.css").resolve()
    assert resolve_static("/js/missing.js", project_root) is None


def test_resolve_static_stays_inside_the_mapped_folder(project_root):
    # web/index.html 은 web/ 안이지만 /js/ 가 가리키는 web/js/ 밖이다.
    assert resolve_static("/js/../index.html", project_root) is None
    assert resolve_static("/js/../css/app.css", project_root) is None


def test_server_binds_loopback_only_by_default(base_url):
    assert base_url.startswith("http://127.0.0.1:")


def test_second_server_cannot_share_a_busy_port(base_url, project_root):
    port = int(base_url.rsplit(":", 1)[1])
    cfg = load_config(project_root, ["--port", str(port)], environ={})
    with pytest.raises(OSError):
        make_server(cfg, None)
