"""HTTP 서버: 정적 파일과 /api/health.

M1 에서 이 파일에 /api/session/* 과 /api/turn(NDJSON)이 더해진다. 그때 쓸 orchestrator 는
make_server 가 받아서 서버 객체에 들고 있는다(M0 에서는 쓰지 않는다).
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .config import Config

# URL 첫 마디 -> 그 URL 이 열어 주는 폴더(프로젝트 루트 기준). 파일은 이 폴더 안에서만 나간다.
STATIC_DIRS = {
    "js": Path("web") / "js",
    "css": Path("web") / "css",
    "shared": Path("shared"),
    "docs": Path("docs"),
}
INDEX_FILE = Path("web") / "index.html"

# 시스템(Windows 레지스트리 등)에 맡기지 않고 우리가 정한 표로만 MIME 을 정한다.
_MIME = mimetypes.MimeTypes()
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".md", "text/markdown")):
    _MIME.add_type(_type, _ext)


def content_type(path: Path) -> str:
    kind, _ = _MIME.guess_type(path.name)
    kind = kind or "application/octet-stream"
    if kind.startswith("text/") or kind == "application/json":
        kind += "; charset=utf-8"
    return kind


def resolve_static(url_path: str, root: Path) -> Path | None:
    """URL 경로를 실제 파일로 바꾼다. 매핑된 폴더 밖으로 나가거나 파일이 없으면 None.

    퍼센트 인코딩은 한 번만 푼다(`%2e%2e` 같은 우회를 막고 한글 파일명을 받기 위해).
    푼 뒤에 resolve() 한 결과가 그 URL 이 열어 주는 폴더 안에 있을 때만 돌려준다.
    """
    path = unquote(url_path)
    if "\x00" in path:
        return None
    if path == "/":
        target = root / INDEX_FILE
        return target.resolve() if target.is_file() else None

    head, sep, rel = path.lstrip("/").partition("/")
    if not sep or head not in STATIC_DIRS:
        return None
    base = (root / STATIC_DIRS[head]).resolve()
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    if not target.is_relative_to(base):
        return None
    if target.is_dir():
        target = target / "index.html"
    return target if target.is_file() else None


class _Handler(BaseHTTPRequestHandler):
    server: "AppServer"

    def do_GET(self) -> None:
        url_path = self.path.split("?", 1)[0].split("#", 1)[0]
        if url_path == "/api/health":
            cfg = self.server.cfg
            self._send(200, "application/json", json.dumps(
                {"ok": True, "llm": "fake" if cfg.use_fake_llm else "claude", "model": cfg.model}
            ).encode("utf-8"))
        elif url_path.startswith("/api/"):
            self.send_error(404)
        else:
            self._serve_static(url_path)

    def _serve_static(self, url_path: str) -> None:
        path = resolve_static(url_path, self.server.cfg.root)
        if path is None:
            self.send_error(404)
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self._send(200, content_type(path), data)

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 개발 중에 고친 JS/CSS 가 캐시에 남아 옛 모습으로 보이지 않게 한다.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class AppServer(ThreadingHTTPServer):
    # Windows 에서 SO_REUSEADDR 은 이미 쓰는 포트에 또 바인딩되게 해서 서버 두 개가 한 포트에 뜬다. 끈다.
    allow_reuse_address = os.name != "nt"

    def __init__(self, cfg: Config, orchestrator: object | None) -> None:
        self.cfg = cfg
        self.orchestrator = orchestrator
        # "::1" 은 IPv6 소켓이어야 열린다(기본 IPv4 소켓이면 주소 오류). 소켓은 super().__init__ 가 만든다.
        self.address_family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET
        super().__init__((cfg.host, cfg.port), _Handler)


def make_server(cfg: Config, orchestrator: object | None) -> ThreadingHTTPServer:
    return AppServer(cfg, orchestrator)
