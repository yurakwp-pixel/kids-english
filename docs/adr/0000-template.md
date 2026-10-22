# ADR-0000: ADR 템플릿과 쓰는 법

**Status:** Accepted
**Date:** 2026-10-22
**Deciders:** 부모(개발자)

> ADR(Architecture Decision Record)은 "왜 이렇게 만들었나"를 한 결정에 한 장씩 남기는 짧은 기록이다.
> 코드는 *무엇을* 하는지 보여 주지만, *왜 다른 방법을 버렸는지*는 보여 주지 못한다. 그 빈칸을 ADR이 채운다.

## 쓰는 규칙

- 파일 이름: `docs/adr/NNNN-짧은-영문-이름.md` (예: `0001-local-claude-via-agent-sdk.md`). 번호는 다시 쓰지 않는다.
- 새 ADR을 만들면 `docs/index.html`의 `DOCS` 배열에 한 줄을 더한다.
- 결정을 뒤집을 때는 옛 ADR을 지우지 않는다. 새 ADR을 쓰고, 옛 ADR의 Status를 `Superseded by ADR-NNNN`으로 바꾼다.
- 측정한 숫자(지연 p50/p95 등)가 있으면 Context나 Consequences에 그대로 적는다. "빠르다" 대신 "p50 2.1초".
- 이 앱에서 예정된 ADR

| 번호 | 주제 | 마일스톤 |
|---|---|---|
| 0001 | 로컬 Claude Code를 Agent SDK로 상주시켜 부른다, 격리 옵션, bench 결과 | M1 |
| 0002 | 표준 라이브러리 서버와 asyncio LLM 액터 스레드 | M1 |
| 0003 | 턴 계약과 카탈로그(LLM은 SVG를 만들지 않는다) | M2 |
| 0004 | fastpath 문법과 exact/partial 판정 | M2 |

---

아래부터가 복사해서 쓰는 틀이다.

```markdown
# ADR-NNNN: [결정 제목]

**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-NNNN
**Date:** YYYY-MM-DD
**Deciders:** [누가 정했나]

## Context
[지금 어떤 상황인가? 어떤 제약과 힘이 작용하나?
 - 이 앱의 제약(127.0.0.1 전용, 추가 비용 0원, 아이 한 명, Windows 경로에 한글·공백)
 - 관련 spec 절과 측정한 숫자]

## Decision
[무엇을 하기로 했나. 한두 문장으로 먼저 말하고, 필요한 설정 값을 그대로 적는다.]

## Alternatives (검토한 대안)

### 대안 A: [이름]
| 기준 | 평가 |
|---|---|
| 복잡도 | 낮음 / 중간 / 높음 |
| 비용 | [구독 쿼터, 돈] |
| 아이 경험(지연·안전) | [평가] |
| 이해하기 쉬움 | [부모가 따라갈 수 있나] |

**장점:** [목록]
**단점:** [목록]

### 대안 B: [이름]
[같은 형식]

## Trade-off Analysis
[대안 사이에서 무엇을 얻고 무엇을 포기했는지, 왜 그쪽을 골랐는지]

## Consequences
- 쉬워지는 것: [...]
- 어려워지는 것: [...]
- 나중에 다시 볼 것: [언제, 어떤 신호가 오면]

## Action Items
1. [ ] [구현 단계]
2. [ ] [후속 확인]
```
