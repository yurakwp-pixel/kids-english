"""HTTP 서버: 정적 파일, GET /api/health, POST /api/session/start · /api/turn · /api/session/end.

핸들러는 얇게 둔다. 턴 처리는 모두 `server.turn.TurnOrchestrator` 가 하고, 여기서는 본문을 읽어 넘기고 줄을 쓴다.

POST 규칙:
- 본문은 JSON 객체 하나(Content-Type 은 보지 않는다). 64 KB 까지. 못 읽으면 400 `{"ok":false,"error":"bad_json"}`,
  Content-Length 가 없으면 411, 너무 크면 413. 요청 값이 틀리면 400 과 오류 코드(bad_session_id, bad_turn_id ...).
- `/api/session/start {session_id}` → `{"ok":true}`, `/api/session/end {session_id}` → `{"ok":true}`.
- `/api/turn` → 200 `application/x-ndjson`. 줄마다 쓰고 flush 한다: 1줄 ack 은 LLM 을 부르기 전에 나간다.
  HTTP/1.0 응답이라 본문 길이 대신 연결을 닫아 끝을 알린다.
- 오케스트레이터 없이 만든 서버(M0 테스트)는 POST 에 503.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .config import Config
from .turn import BadRequest, TurnOrchestrator

MAX_BODY_BYTES = 64 * 1024
MAX_DISCARD_BYTES = 1024 * 1024  # 너무 큰 본문도 이만큼은 읽어 버린 뒤 413 을 보낸다(안 읽고 닫으면 연결이 리셋된다)
JSON_TYPE = "application/json; charset=utf-8"
NDJSON_TYPE = "application/x-ndjson"
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def ndjson_line(obj: dict) -> bytes:
    """한 줄 JSON + 줄바꿈. ensure_ascii=True 라서 한글은 \\uXXXX 로 나가고, 짝 없는 서로게이트가 있어도 오류가 없다."""
    return (json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii")


def _report_bug(where: str, error: BaseException) -> None:
    """서버 콘솔에 예외 종류와 위치(파일:줄)만 남긴다. 예외 메시지 글에는 아이 말이 섞일 수 있어 쓰지 않는다."""
    print(f"[kids-english] {where}: {type(error).__name__}", file=sys.stderr)
    traceback.print_tb(error.__traceback__, file=sys.stderr)

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

    def do_POST(self) -> None:
        raw = self._read_body()  # 경로와 상관없이 먼저 읽는다: 안 읽은 본문을 남기고 닫으면 응답 대신 리셋이 갈 수 있다
        if raw is None:
            return
        route = _POST_ROUTES.get(self.path.split("?", 1)[0].split("#", 1)[0])
        if route is None:
            self.send_error(404)
            return
        orchestrator = self.server.orchestrator
        if orchestrator is None:
            self._send_json(503, {"ok": False, "error": "no_orchestrator"})
            return
        try:
            body = json.loads(raw)  # bytes: UTF-8/16/32 를 알아서 읽는다. 깨진 바이트는 ValueError
        except (ValueError, RecursionError):
            body = None
        if not isinstance(body, dict):
            self._send_json(400, {"ok": False, "error": "bad_json"})
            return
        route(self, orchestrator, body)

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(411, {"ok": False, "error": "length_required"})
            return None
        if length > MAX_BODY_BYTES:
            if length <= MAX_DISCARD_BYTES:
                self.rfile.read(length)
            self._send_json(413, {"ok": False, "error": "too_large"})
            return None
        return self.rfile.read(length)

    def _session_start(self, orchestrator: TurnOrchestrator, body: dict) -> None:
        self._session_call(orchestrator.start_session, body)

    def _session_end(self, orchestrator: TurnOrchestrator, body: dict) -> None:
        self._session_call(orchestrator.end_session, body)

    def _session_call(self, call, body: dict) -> None:
        try:
            call(body.get("session_id"))
        except BadRequest as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        self._send_json(200, {"ok": True})

    def _turn(self, orchestrator: TurnOrchestrator, body: dict) -> None:
        lines = orchestrator.handle(body)
        try:
            try:
                first = next(lines)  # 요청 검사는 여기서 끝난다: 틀렸으면 헤더를 보내기 전에 400
            except BadRequest as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            except Exception as e:
                _report_bug("turn failed before the first line", e)
                self._send_json(500, {"ok": False, "error": "internal"})
                return
            self.send_response(200)
            self.send_header("Content-Type", NDJSON_TYPE)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._write_line(first)
            for line in lines:
                self._write_line(line)
        except _CLIENT_GONE:
            pass  # 아이 화면이 먼저 닫혔다. 남은 턴은 버린다(아래 close 가 잠금을 푼다).
        finally:
            lines.close()

    def _write_line(self, obj: dict) -> None:
        self.wfile.write(ndjson_line(obj))
        self.wfile.flush()

    def _send_json(self, status: int, obj: dict) -> None:
        self._send(status, JSON_TYPE, json.dumps(obj).encode("utf-8"))

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


_POST_ROUTES = {
    "/api/session/start": _Handler._session_start,
    "/api/session/end": _Handler._session_end,
    "/api/turn": _Handler._turn,
}


class AppServer(ThreadingHTTPServer):
    # Windows 에서 SO_REUSEADDR 은 이미 쓰는 포트에 또 바인딩되게 해서 서버 두 개가 한 포트에 뜬다. 끈다.
    allow_reuse_address = os.name != "nt"

    def __init__(self, cfg: Config, orchestrator: TurnOrchestrator | None) -> None:
        self.cfg = cfg
        self.orchestrator = orchestrator
        # "::1" 은 IPv6 소켓이어야 열린다(기본 IPv4 소켓이면 주소 오류). 소켓은 super().__init__ 가 만든다.
        self.address_family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET
        super().__init__((cfg.host, cfg.port), _Handler)


def make_server(cfg: Config, orchestrator: TurnOrchestrator | None) -> ThreadingHTTPServer:
    return AppServer(cfg, orchestrator)
