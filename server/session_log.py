"""세션 로그: `data/sessions/<session_id>.jsonl` 에 사건 하나를 한 줄 JSON 으로 덧붙인다.

- 저장하는 것은 텍스트뿐이다. 오디오와 음성인식 엔진 정보는 넣지 않는다(무엇을 넣을지는 턴 처리기가 정한다).
- 줄마다 `ts`(그 PC 시각, 시간대 포함 ISO 8601)를 맨 앞에 자동으로 넣는다.
- `json.dumps(ensure_ascii=True)` 로 쓴다: 한글은 `\\uXXXX` 로 적히고, 짝 없는 서로게이트가 섞여도 쓰기가 실패하지 않는다.
- session_id 는 파일 이름이 되므로 `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` 만 받는다(경로 문자, 점, Windows 예약 이름 거부).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_WINDOWS_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])", re.IGNORECASE)


def valid_session_id(session_id: object) -> bool:
    """브라우저가 만드는 `2026-10-22_1905` 같은 id. 파일 이름으로 써도 안전할 때만 True."""
    return (
        isinstance(session_id, str)
        and _SESSION_ID.fullmatch(session_id) is not None
        and _WINDOWS_RESERVED.fullmatch(session_id) is None
    )


class SessionLog:
    def __init__(self, dir: Path) -> None:  # noqa: A002 (인터페이스 이름 그대로)
        self.dir = Path(dir)
        self._lock = threading.Lock()  # 여러 HTTP 스레드가 같은 파일에 써도 줄이 섞이지 않게

    def path_for(self, session_id: str) -> Path:
        if not valid_session_id(session_id):
            raise ValueError("bad session id")  # 값은 메시지에 넣지 않는다
        return self.dir / f"{session_id}.jsonl"

    def append(self, session_id: str, event: dict) -> None:
        """한 줄을 덧붙인다. 잘못된 id 는 ValueError, 디스크 문제는 OSError(부르는 쪽이 놀이를 멈추지 않게 처리)."""
        path = self.path_for(session_id)
        ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
        line = json.dumps({"ts": ts, **{k: v for k, v in event.items() if k != "ts"}}, ensure_ascii=True)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
