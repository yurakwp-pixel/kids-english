# kids-english

6~7세 아이가 영어로 말하면 놀이판이 움직이는, 우리 집 전용 영어 말하기 놀이 앱이다.
Python 표준 라이브러리 서버 + 순수 HTML/CSS/JS. LLM은 이 PC의 Claude Code 구독(Haiku)을 쓴다.

- 설계: [`docs/superpowers/specs/2026-10-22-kids-english-design.md`](docs/superpowers/specs/2026-10-22-kids-english-design.md)
- 구현 계획: [`docs/superpowers/plans/2026-10-22-m0-m2.md`](docs/superpowers/plans/2026-10-22-m0-m2.md)
- 개발 규칙: [`CLAUDE.md`](CLAUDE.md)

## 준비

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

선택: 구독 로그인 대신 장기 토큰을 쓰려면 `.env.example`을 `.env`로 복사하고, 직접 `claude setup-token`으로 받은 값을 `CLAUDE_CODE_OAUTH_TOKEN=` 뒤에 넣는다. `.env`는 git에 올라가지 않는다.

## 실행

```bash
.venv/Scripts/python run.py            # http://127.0.0.1:8771
.venv/Scripts/python run.py --fake-llm # Claude 대신 FakeLLM
.venv/Scripts/python run.py --port 9000
```

`GET /api/health`가 `{"ok": true, "llm": "fake" | "claude", "model": "haiku"}`를 돌려주면 서버가 떠 있는 것이다.

## 테스트

```bash
.venv/Scripts/python -m pytest -q          # 실제 Claude를 부르는 live 테스트는 기본 제외
node --test tests/js/*.test.mjs
```
