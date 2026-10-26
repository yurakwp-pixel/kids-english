# HANDOFF — kids-english 인수인계

> 마지막 갱신: 2026-10-26
> 새 세션은 이 파일 → `CLAUDE.md` → 맡은 작업 레인(아래 4절)의 관련 문서 순서로 읽고 시작한다.
> 작업을 시작하면 4절 표의 "담당" 칸을 고쳐서 커밋하고, 끝나면 "상태" 칸과 3절을 갱신한다. 이 파일이 세션 사이의 유일한 조정 지점이다.

---

## 1. 한눈에

| 항목 | 내용 |
|---|---|
| 무엇 | 6~7세 아이 한 명이 영어로 말하면 놀이판(멍멍이 "Chomp" + 아기 공룡)이 움직이는 집 전용 영어 말하기 앱. "영어 한마디가 곧 조종 버튼" |
| 구조 | Python 표준 라이브러리 서버(127.0.0.1:8771) + 순수 HTML/CSS/JS. 턴마다 NDJSON 2줄: 1줄 fastpath 즉시 반응, 2줄 로컬 Claude Code(haiku) 답 |
| 진행 | **M0~M2 완료, `main`에 머지·GitHub 공개됨.** 커밋 43개, pytest 1161 통과(live 2개 제외), node 190 통과 |
| 다음 | 아이 실사용 테스트 → M3(응답 속도 + 학습 보조 기능) |
| 실행 | `실행.bat` 더블클릭(또는 바탕화면 바로가기). 브라우저는 **Edge**, 주소는 **http://127.0.0.1:8771/** |
| 문서 | 서버를 켜고 http://127.0.0.1:8771/docs/ (C4 3장, 시퀀스 2장, ADR 4개, 워크스루 M0~M2) |

## 2. 지금 진행 중인 일

- 없음. (작업을 시작한 세션은 4절 표의 "담당"을 채우고 여기에 한 줄로 적는다.)

### 완료: GitHub 공개 저장소 (2026-10-26)
- 원격: `origin` = https://github.com/yurakwp-pixel/kids-english (public), `main`이 `origin/main`을 추적한다.
- 공개 전에 이력을 재작성했다: 모든 커밋에서 벤치마크 문서, 아이 개인 정보 표현, PC 경로를 제거했고 작성자를 GitHub noreply 이메일로 바꿨다. **이미 push한 이력은 다시 재작성하지 않는다(force-push 금지).**
- 이 폴더의 로컬 git 설정은 noreply 이메일이다. **다른 이메일로 커밋하지 말 것.**
- 벤치마크 문서는 로컬 `docs/benchmark/`에만 있고 git에서 제외된다(`.gitignore`).
- push는 사용자가 요청할 때만 한다. 레인 브랜치는 로컬에서 머지한 뒤 `main`을 push한다.

## 3. 반드시 지킬 것 (요약 — 원문은 `CLAUDE.md`, spec)

- **비용 0원:** LLM은 사용자 본인의 Claude Code 구독(haiku)만. API 키 과금 경로 금지. `scripts/bench_llm.py`와 `pytest -m live`는 쿼터를 쓰므로 **사용자 승인 후에만** 실행한다.
- **격리:** 아이 대화용 Claude는 `setting_sources=[]`, `tools=[]`, 내장 플러그인 끔, env 정리, `apiKeySource != none`이면 영구 거부. 이 격리를 약하게 만드는 변경은 하지 않는다(ADR-0001).
- **자격증명:** `.env`(`CLAUDE_CODE_OAUTH_TOKEN`)는 읽거나 출력하지 않는다. 토큰은 사용자가 직접 넣는다.
- **공개 저장소:** 이제 public이다. 커밋에 아이의 이름·유치원·가족 정보 같은 개인 정보, PC 사용자 경로, 개인 이메일, 비밀값, 실제 회사에 대한 미검증 주장을 넣지 않는다. 경로는 상대 경로나 `~/`로 쓴다.
- **아이 UX:** 버튼 120px 이상(마이크 160px 이상), 글씨 28px 이상, "Wrong"/"No"·게임오버 금지, 1초 3회 넘는 깜빡임 금지, 아이 화면에 외부 리소스 금지. 멍멍이는 음식만 먹는다.
- **TDD + 마일스톤 문서:** 실패 테스트 먼저. 마일스톤마다 `docs/walkthrough/mN-*.md`, C4·시퀀스 갱신, ADR. 문서의 `파일:줄` 인용은 코드와 맞아야 한다.
- **커밋:** 메시지 끝에 빈 줄 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **실행 환경 주의:** `localhost` 대신 `127.0.0.1`(IPv6 지연). 앱 창은 하나만(창이 둘이면 세션이 바뀌어 Claude가 재시작). Claude 앱 안의 미리보기 브라우저는 영어 TTS 음성이 없으므로 실제 테스트는 Edge로. 미리보기 패널이 숨겨져 있으면 애니메이션 프레임이 멈춘다.

## 4. 병렬 작업 레인 (서로 파일이 겹치지 않게 나눔)

규칙: **한 레인 = 한 세션 = 한 브랜치(가능하면 워크트리).** 아래 "소유 파일"만 고친다. 다른 레인의 소유 파일을 고쳐야 하면 먼저 이 파일 5절에 적고 해당 레인과 순서를 정한다. 테스트 파일은 고치는 모듈의 테스트만 소유한다.

| 레인 | 목표 | 소유 파일 | 선행 조건 | 담당 | 상태 |
|---|---|---|---|---|---|
| **A. 응답 속도 (M3-1)** | LLM p50 2.7초 → 2.5초 이하. 시스템 프롬프트 줄이기(현재 레벨 단어만, 카탈로그 요약 압축, 규칙·예시 정리), bench를 실제 프롬프트로 바꿔 20턴 재측정 | `prompts/chomp_system.md`, `server/prompt_builder.py`, `scripts/bench_llm.py`, `tests/py/test_bench_llm.py`, 새 `tests/py/test_prompt_builder.py`(새 프롬프트 테스트는 여기에), `docs/adr/0004-fastpath.md`의 지연 절. `test_turn_m2.py` 안의 기존 프롬프트 테스트(`test_system_prompt_*`, `test_prompt_examples_*`)는 고칠 수 있지만 다른 테스트는 C 소유 | 재측정은 사용자 쿼터 승인 | — | 대기 |
| **B. 단어장 확장** | 슈니비니 카드 A·B 전체 + 핵심 단어로 레벨 1~2 확장(목표 700~900의 첫 단계). JSON 한 줄 = 단어 하나 | `content/words/*.json` (새 파일 추가 포함) | 사용자가 카드 사진 제공 | — | 대기 |
| **B2. 단어장 로더 보강** | accept 중복 검사를 종류별(동작·감정 / 색·크기·숫자 / 물건)로 나눔("orange" 색과 과일 공존) | `server/words.py`, `tests/py/test_words.py` | B에서 동음이의어가 처음 나올 때 | — | 대기 |
| **C. 학습 보조 (M3 본체)** | "Did you say X or Y?" 확인, 🇰🇷 도와줘 버튼(ko-KR 인식), 8초 무응답 자동 힌트, 따라 말하기 성공 시 알 부화 보상, 비밀 단어 마스킹(`redact`) | `server/turn.py`, 새 `server/asr_policy.py`, `server/canned.py`, `web/js/main.js`, `web/js/mic.js`, `web/js/ui_state.js`, `web/js/turn_end.js`, `tests/py/test_turn.py`·`test_turn_m2.py`(프롬프트 테스트 제외)·`test_canned.py`, 해당 JS 테스트 | 설계 먼저(superpowers brainstorming → spec → plan). 프롬프트 변경이 필요하면 A와 순서 조율 | — | 대기 |
| **D. 서버 백로그 정리** | 최종 리뷰가 M3로 미룬 서버 쪽 Minor: `_read_body` 소켓 타임아웃, `ops_fixed`·`ops_trimmed` 이슈 기록 정확도, fastpath↔validator 계약 테스트, `reply.py` 분리(`ops_check.py`) 검토 | `server/app.py`, `server/reply.py`, `server/fastpath.py`, `server/ids.py`, 각 테스트 | 없음 | — | 대기 |
| **E. 화면 백로그 정리** | 최종 리뷰가 미룬 화면 쪽 Minor: director 큐가 렌더 예외에 막히지 않게, 음식 아닌 대상 물기 애니메이션 차단, 입 모양 속도 3회/초 이하, 죽은 코드(face) 정리 | `web/js/scene/director.js`, `web/js/scene/render.js`, `web/js/scene/sprites.js`, `web/js/scene/frame.js`, `web/css/app.css`, 관련 테스트 | 없음 (main.js는 C 소유) | — | 대기 |
| **F. 문서 백로그** | `docs/superpowers/rulings-and-backlog-m0-m2.md` 2절의 문서 항목(M2 워크스루 "순수 함수라서 서버와 화면이 어긋나지 않는다" 과장 수정, ADR 문구 등) | `docs/` (단, 다른 레인이 같은 문서를 고치는 중이면 그 레인 우선) | 없음 | — | 대기 |
| **G. 15분 세션 (M4)** | 워밍업 3문장 → 놀이 → 그림책 → Good night, 하루 1회 잠금 | 새 `server/session.py`·`server/curriculum.py`·`web/js/flow.js` 등 | **C 완료 후** (turn.py·main.js 공유) | — | 보류 |
| **H. 태블릿 지원** | LAN HTTPS, 바인딩 정책 재설계 | `server/config.py`, `server/app.py`, `run.py` | 사용자 결정 필요(보안 정책). D와 app.py 공유 → D 이후 | — | 보류 |

**바로 병렬로 돌려도 안전한 조합:** A + B + D + E + F. (C는 설계 단계까지는 병렬 가능, 구현은 A와 프롬프트 순서 조율.)
**머지 순서 권장:** D, E, F(작고 독립) → A → B → C. 각 머지 후 `pytest -q`와 `node --test tests/js/*.test.mjs` 모두 통과 확인.

## 5. 레인 간 조율 메모

- (비어 있음) 다른 레인의 파일을 건드려야 하면 여기에 "레인 X → 파일 Y: 이유, 원하는 시점"을 적는다.

## 6. 사용자에게 받아야 할 것

1. 아이 실사용 테스트 결과 (README의 체크리스트 5가지)
2. 슈니비니 카드 A·B 전체 목록 사진 → 레인 B
3. 레인 A 재측정용 쿼터 사용 승인 (haiku 약 20턴)

## 7. 참고 문서

| 문서 | 용도 |
|---|---|
| `docs/superpowers/specs/2026-10-22-kids-english-design.md` | 목표·반목표·결정의 원본 (구속력 있음) |
| `docs/superpowers/plans/2026-10-22-m0-m2.md` | M0~M2 구현 계획(완료) |
| `docs/superpowers/rulings-and-backlog-m0-m2.md` | 구현 중 내린 판정 44건 + M3로 미룬 항목 전체 목록 |
| `docs/adr/0001`~`0004` | 로컬 Claude 연결·격리, 서버 동시성, 턴 계약·카탈로그, fastpath |
| `docs/walkthrough/m0`~`m2` | 개념별 설명(코드 지도·직접 해보기 포함) |
| `README.md` | 실행·테스트·아이 테스트 체크리스트 |
| `CLAUDE.md` | 개발 규칙과 아이 UX 규칙 |
