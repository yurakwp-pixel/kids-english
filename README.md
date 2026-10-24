# kids-english

6~7세 아이가 영어로 말하면 놀이판이 움직이는, 우리 집 전용 영어 말하기 놀이 앱이다.
Python 표준 라이브러리 서버 + 순수 HTML/CSS/JS. LLM은 이 PC의 Claude Code 구독(Haiku)을 쓴다.

- 설계: [`docs/superpowers/specs/2026-10-22-kids-english-design.md`](docs/superpowers/specs/2026-10-22-kids-english-design.md)
- 구현 계획: [`docs/superpowers/plans/2026-10-22-m0-m2.md`](docs/superpowers/plans/2026-10-22-m0-m2.md)
- 개발 규칙: [`CLAUDE.md`](CLAUDE.md)
- 구조 문서(다이어그램, ADR, 워크스루): 서버를 켜고 `http://127.0.0.1:8771/docs/`

## 준비

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

선택: 구독 로그인 대신 장기 토큰을 쓰려면 `.env.example`을 `.env`로 복사하고, 직접 `claude setup-token`으로 받은 값을 `CLAUDE_CODE_OAUTH_TOKEN=` 뒤에 넣는다. 토큰은 사용자가 직접 넣는다. `.env`는 git에 올라가지 않는다.

## 실행

둘 중 하나로 켠다.

- **Claude 앱의 미리보기:** 이름 `kids-english`(`.claude/launch.json`). `http://127.0.0.1:8771`로 열린다.
- **터미널:**

  ```bash
  .venv/Scripts/python run.py            # 진짜 Claude(구독 쿼터를 쓴다)
  .venv/Scripts/python run.py --fake-llm # Claude 대신 FakeLLM(쿼터 0, 화면 개발용)
  .venv/Scripts/python run.py --port 9000
  ```

브라우저 주소는 **`http://127.0.0.1:8771/`** 로 연다. `localhost`로 열면 Windows가 IPv6(`::1`)부터 시도하고 서버는 `127.0.0.1`에만 열려 있어서, 새 연결마다 약 0.3초(실측 314 ms)가 더 든다. 아이 화면의 즐겨찾기도 `127.0.0.1`로 만든다.

| 주소 | 누가 | 무엇 |
|---|---|---|
| `http://127.0.0.1:8771/` | 아이 | 놀이 화면. 🎤를 누르고 영어로 말한다 |
| `http://127.0.0.1:8771/?debug=1` | 부모 | 같은 화면 + 디버그 패널. 마이크 대신 글자로 말을 넣고(쉼표로 여러 후보), 서버에서 온 줄과 도착 시각(ms), 장면에 적용된 ops를 본다 |
| `http://127.0.0.1:8771/docs/` | 부모 | 구조 문서 뷰어 |
| `http://127.0.0.1:8771/api/health` | 부모 | `{"ok": true, "llm": "fake" \| "claude", "model": "haiku"}`면 서버가 떠 있다 |

- `--fake-llm`이어도 fastpath(단어장과 맞는 말), 알 부화, 검증기는 진짜와 똑같이 돈다. 바뀌는 것은 LLM이 할 대답뿐이다.
- 단어(`content/words/*.json`)나 카탈로그(`shared/catalog.json`)를 고치면 서버를 다시 켠다. 켤 때 한 번 읽는다.

## 테스트

```bash
.venv/Scripts/python -m pytest -q          # 실제 Claude를 부르는 live 테스트는 기본 제외
node --test tests/js/*.test.mjs
```

## 아이 테스트 체크리스트 (M2)

마이크 테스트는 부모가 아이와 직접 한다. 부모는 옆에서 `?debug=1` 창을 하나 더 열어 두면 지연과 줄을 볼 수 있다(같은 세션이 아니어도 된다).

1. [ ] **5분 동안 말로 장면을 5번 바꾼다.** 예: "Hello, Chomp!"(알이 깨진다), "Kick the ball!", "Big red egg!", "Chomp the apple!", "Put on your socks!"
2. [ ] **간단한 명령은 0.5초 안에 반응한다.** 말이 끝나고 인식이 확정된 뒤부터 잰다(인식 확정 자체에 0.5~1초가 걸리는 것은 브라우저 몫). 단어장에 있는 말이면 화면이 바뀌기까지 약 0.06초다(브라우저가 보낸 때부터, Task 15 실측 55–72 ms).
3. [ ] **"틀렸어" 느낌이 없다.** "kick ball"처럼 조금 틀려도 장면은 바뀌고, 멍멍이가 바른 문장을 다정하게 들려준다(예: "Kick the ball! Your turn!"). "Wrong", "No"는 나오지 않는다.
4. [ ] **알 부화를 한 번 본다.** 세션의 첫 영어 한마디에 알이 흔들리고 → 금이 가고 → 깨지며 아기 공룡이 튀어나온다. 번쩍이는 효과가 없어야 한다. 화면을 새로고침하면 새 세션이라 다시 볼 수 있다.
5. [ ] **한국어로 한마디 해 본다.** 장면은 그대로이고, 멍멍이가 따라 할 영어를 준다(예: "Red egg? Say: red egg!"). 아이가 그 영어를 말하면 그때 장면이 바뀐다. (?)를 누르면 "뜻 → English" 힌트가 말풍선 밖에 보인다.

알아 두기:
- 실제 음성은 영어(en-US) 인식기 하나로만 듣는다. 아이가 한국어로 말하면 엉뚱한 영어 글자로 들릴 수 있고, 그러면 멍멍이가 "Kick or jump?"처럼 영어로 고를 수 있게 묻거나 따라 할 문장을 준다. 한국어 도움 버튼(ko-KR)은 M3에 넣는다.
- LLM이 답하는 말(단어장 밖의 말, 조금 틀린 말)은 지금 보통 2~3초 걸린다(목표 2.5초를 조금 넘는다, M3에서 줄인다).
