# CLAUDE.md — Polyrus

> 이 파일은 Claude Code가 자동으로 읽는 프로젝트 지침서입니다.
> Polyrus를 이어서 구현할 때 **가장 먼저** 읽으세요.

## 미션

Polyrus는 최고급 모델(Claude Max·Codex 5.5 Pro 등) 파워유저를 위한
**검증된 비-패스(No-Pass) 추론 에이전트**다. 시장 주도 에이전트(OpenClo·Hermes)가
자율성·메모리로 경쟁할 때, Polyrus는 *신뢰성*으로 경쟁한다:
사안의 진실값에 따라 반박하고, 작업을 회피·중도포기하지 않으며, 검증 없이 행동하지 않는다.

## 단 하나의 핵심 아이디어

기존 에이전트 루프(OpenClaw·Hermes·Claude Code 포함)는 **모델이 "끝"이라고 선언하면**
(텍스트-only 응답) 종료된다. 이것이 조기종료/패스 실패의 근원이다.

Polyrus는 루프의 **종료 조건을 뒤집는다**:

> 완료는 모델의 주장이 아니라 **검증기 뱅크의 판정**이다.

이 한 가지가 전체 설계를 끌고 간다. 코드를 쓸 때 항상 이 불변식을 지켜라.

## 루프 불변식 (의사코드)

```
while not ledger.all_items_verified():
    item = ledger.next_open_item()
    candidates = arms.generate(item, k=adaptive_k(item.risk))   # 병렬 팔 + 콜드스타트
    best = arms.select(candidates)
    verdict = verifiers.run(best, item.dod)                     # 검증기 뱅크 T1-T4
    if verdict.passed:
        ledger.close(item, verdict)                            # 확신도 = 티어 가중
    elif retries_left(item):
        arms.diversify(item)                                  # M4 다양화 재시도
    else:
        escalation.raise_to_human(item, verdict.blocker)       # M3: 포기 대신 에스컬레이션
# 종료: 모든 항목이 검증-완료 또는 명시적 에스컬레이션. '조용한 패스' 없음.
```

실제 코드는 `src/polyrus/core/loop.py`(순수 코어)에 있고, Claude Code 래퍼는 `adapters/claude_code/stop_hook.py`가 이 루프를 호출한다. 단, `adaptive_k`는 `budget.py`의 예산 봉투에서 상한을 읽고, 예산 소진은 `escalation.raise_to_human`으로 라우팅된다(No-Silent-Stop). 종료는 셋(검증완료/에스컬레이션/예산경계-에스컬레이션) 중 하나로만.

## 배포 폼팩터 (구현 전 확정 — Wrap-First)

첫 웨지는 **독립 에이전트가 아니라 Claude Code Stop-hook 래퍼**다. 그래서 코어는 *임베더블 검증 엔진*
(I/O 가정 0)이고, 배포 표면은 *얇은 어댑터*다. Claude Code `Stop` 훅이 `block + reason`을 반환하면
모델을 못 멈추게 + 이어가게 하는데, 이게 종료조건 역전의 집행 씨앗이다. **새 에이전트 루프를 짜지 마라 —
기존 에이전트를 감싸라.** 문어 병렬 팔/창의 발산은 Phase 1의 독립 CLI(소유 모드)로 graduate.
(상세 → `DESIGN.md` §10, `../20260618_1141_Polyrus_배포전략_wrap-first_기획안.md`)

## 아키텍처 맵

- `src/polyrus/core/loop.py` — No-Pass 오케스트레이터. **순수 함수 경계** — `(태스크,DoD,예산)` →
  `(verdict,확신도,코퍼스레코드)`. stdin/모델 I/O를 떠안지 않는다. **(핵심)**
- `src/polyrus/adapters/` — **모델/에이전트 비종속 경계**(흡수방지 해자의 코드적 실체):
  - `claude_code/stop_hook.py` — 첫 어댑터(웨지). Stop 훅 → 코어 호출 → 미검증 시 `block+reason` 재주입.
  - `cli/` — Phase 1, 독립 CLI(루프 소유, 문어/발산).
  - `types.py`의 `AgentAdapter`/`ModelClient` 프로토콜을 구현한다.
- `src/polyrus/budget.py` — 예산 봉투(max_tokens·N·k·wall_clock) + 막힘 감지. 비정지 보장.
- `src/polyrus/ledger.py` — M1 완료 원장 (DoD + 검증 게이팅, 외부 기억) + **보정 코퍼스 emit**(리댁션).
- `src/polyrus/core/` — 추론 코어(문어): `arms.py`(병렬 팔+콜드스타트), `coordinator.py`(이견 보존).
- `src/polyrus/verifiers/` — 검증 뱅크(까마귀, **진짜 해자**):
  - `base.py` — Verifier 프로토콜, Tier, VerifierResult. **계약을 먼저 읽어라.**
  - `registry.py` — 주장 → 검증기 라우팅 (티어별).
  - `code/` — 0단계 코드 검증기 스택 (T1-T4).
- `src/polyrus/dod.py` — DoD 생성기 (스펙 → 동결 수용 테스트). '정당성'의 첫 방어선.
- `src/polyrus/gates.py` — 증거조사 게이트 (휴먼인루프, M3 인스턴스).
- `src/polyrus/escalation.py` — M3 에스컬레이션.
- `src/polyrus/sandbox.py` — 실행 샌드박스 (subprocess/Docker).
- `src/polyrus/models.py` — 모델 비종속 클라이언트 (OpenAI 호환 + 어댑터 + 폴백).
- `src/polyrus/types.py` — 공유 타입. **여기부터 읽어라.**

## 컨벤션

- Python 3.11+. 모든 public 함수/메서드에 타입 힌트. 파일 상단에 `from __future__ import annotations`.
- 검증기는 `Verifier` 프로토콜(`verifiers/base.py`)을 구현하고 `VerifierBank`에 등록한다.
- 검증기는 **생성기와 독립**이어야 한다. 가능하면 비-LLM 오라클(컴파일러/런타임/정적분석/메타데이터)을 써라.
- 검증기는 자기 `reliability`(신뢰도)를 정직하게 보고한다. 약한 검증기의 PASS는 확신도 1이 아니다.
- 외부 명령은 반드시 `Sandbox`를 통해 실행한다 (직접 `subprocess` 금지). 셸 인젝션/경로 탈출 주의.
- 린트 `ruff`, 테스트 `pytest`, 타입 `pyright`/`mypy`.

## No-Pass 규칙 (너 자신에게도 적용된다)

이 레포를 이어서 짤 때 Polyrus의 원칙을 *너에게도* 적용해라:

1. **빈 구현을 '완료'라 하지 마라.** 아직 못 짠 부분은 `raise NotImplementedError`와 `# TODO(phase):`로
   명시하라. 가짜로 통과하는 스텁 금지.
2. **테스트로 완료를 증명하라.** 기능을 추가했으면 그 기능의 테스트가 green이어야 한다.
   `pytest`가 빨간 채로 "됐다"고 하지 마라.
3. **막히면 회피하지 말고 에스컬레이션하라.** 정보가 부족하면 구체적 질문을 남겨라
   ("X를 결정해야 진행 가능").
4. **할루시네이트하지 마라.** 부르는 API/함수가 실제로 존재하는지 확인하라 — 이건 T3가 잡는 바로 그 실패다.

## 시작하기

1. `src/polyrus/types.py`와 `src/polyrus/verifiers/base.py`로 계약을 파악.
2. `ROADMAP.md`의 **Phase 0**부터. 첫 목표: `examples/verified_code_task.py`가 실제로 돌게 만들기.
3. 구현 순서 (Phase 0a 코어 → 0b 래퍼): `types.py`(경계: AgentAdapter/ModelClient/VerifierResult) →
   `sandbox.py` → `verifiers/code/t1_execution.py` → `dod.py` → `ledger.py`(+코퍼스 emit) → `budget.py` →
   `core/loop.py`(순수) → `adapters/claude_code/stop_hook.py`(첫 데모 표면).
   각 검증기를 구현한 뒤 `registry.default_code_bank()`에서 등록 해제(주석 풀기).
4. `pytest -q`로 진행 확인. 스모크 테스트는 구현 전에도 green이어야 한다.

## 실행

```bash
pip install -e ".[dev]"
pytest -q
python examples/verified_code_task.py   # 0단계 데모 (구현 진행에 따라 점점 동작)
```

## v0.4 개념 (DESIGN.md 참고)
- 두 모드(수렴/발산·창의성), 가중치 다이얼 A~D(추천+override, 페르소나/CLAUDE.md와 분리),
  이해 검증(출력 검증의 상류), 멈춤·누락 제거(검증기=루프 종료조건), 장시간 자율(체크포인트),
  자격증명(로컬 우선·env식·키체인·면책 → SECURITY.md, src/polyrus/secrets.py).
