# ROADMAP — Polyrus

각 단계는 *검증 가능한 완료 기준*을 갖는다 (우리 자신의 No-Pass 원칙 적용).

## Phase 0 — 검증된 코드 태스크 (지금) · **Wrap-First**
> 폼팩터 확정: 첫 웨지 = **Claude Code Stop-hook 래퍼**(상세 → `../20260618_1141_Polyrus_배포전략_wrap-first_기획안.md`).
> 독립 하니스가 아니라 *임베더블 코어 + 래퍼 어댑터*로 쪼갠다. 코어는 I/O 가정 0.

### Phase 0a — 임베더블 코어 (어차피 필요)
- [x] `types.py` — **경계.** `AgentAdapter` 프로토콜, `Locality`, `Budget`, `Termination`, `CorpusRecord`, `LoopResult`. (`ModelClient`은 `models.py`에 기존재.)
- [x] `sandbox.py` subprocess 백엔드 (타임아웃·shell=False·`Workspace` 경로탈출 차단).
- [x] `verifiers/code/t1_execution.py` — ruff+pytest 파이프라인 (PASS/FAIL/INCONCLUSIVE, 실측 검증).
- [x] 검증 뱅크 T1-우선 단락(5.7 비용순 사다리) + `default_code_bank()` 등록.
- [x] `budget.py` 상당 — 예산 봉투 + 막힘 감지는 `types.Budget` + `harness`에 구현. **(v0.5 정지 보장)**
- [x] 코어 루프 — 예산경계·종료 3상태·코퍼스 emit을 `harness.py`에 배선(데모 green/escalate 실증). *순수 `core/loop.py` 분리는 0b에서 어댑터와 함께.*
- [x] `store.py` SQLite 영속화 — 보정 코퍼스 플라이휠(5.4) + 원장 결과 + override 라벨. `harness.run(..., store=)` 배선.
- [x] `dod.py` — 구조적 분해(다하위목표) + 동결(굿하트). `Ledger`에 연결. *프로즈→테스트 합성은 Phase 1(LLM).*
- [x] `verifiers/code/t3_api_existence.py` — AST+importlib 환각 API 차단(비-LLM). 뱅크 등록.
- [ ] `verifiers/code/t1_mutation.py` — mutmut 점수 게이트 (테스트 강도 검증).

### Phase 0b — Claude Code 래퍼 어댑터 (첫 데모 표면)
- [x] `adapters/claude_code/stop_hook.py` — Stop 훅 → 단일 검증 패스 → 미검증이면 `{"decision":"block","reason":...}` 재주입, 통과면 `{}` 허용. continue 예산 소진→강제 stop+에스컬레이션(No-Silent-Stop).
- [x] `adapters/claude_code/task_file.py` + `polyrus-stop-hook` 진입점 — `.polyrus/task.json`(모델 밖 완료 원장) 로더. **실제 CLI 스모크 통과**(틀린 코드→block, 올바른 코드→{}).
- [x] `polyrus wrap claude` (settings.json Stop 훅 멱등 등록) + `unwrap`/`status`. 기존 설정 보존, dry-run.
- [x] `polyrus init` — `.polyrus/task.json` 골격 + `.gitignore` 생성, `--wrap`이면 훅 등록까지. 온보딩 마찰(#2) 절반 해소. 골격→로더 파싱 검증.
완료 기준 진행: (a) 통과 시 검증된 완료 ✅, (b) 실패 시 재주입(block) ✅, (c) continue 예산 소진→에스컬레이션 ✅. *실제 Claude Code 세션 라이브 검증은 settings 등록 후.*

### 텔레그램 채널 (6.4 확인 트레이 / 6.1 게이트) — away 유저 폰
- [x] `notify/telegram.py` — 알림 + **원탭 승인/거부**(인라인 버튼 + 롱폴링). 토큰 env(6.3)·리댁션·주입식 transport(테스트 네트워크 0).
- [x] 통합: `EvidenceGate(ask_fn=approval_gate_fn(client))`(게이트 승인), `Escalator(sink=escalation_sink(client))`(M3 알림), `run_hook(..., notifier=)`(예산 소진 핑).
- [ ] 실제 봇 토큰으로 라이브 검증(유저 env 설정 후). 인터랙티브 승인은 webhook 대신 getUpdates 롱폴링(로컬-우선·공개서버 불필요).

### PyPI 배포 준비 (이름·빌드·메타데이터)
- [x] 배포 이름 `polyrus-agent`(`polyrus`는 PyPI 선점됨; import는 `polyrus` 유지). pyproject 메타데이터·classifiers·urls.
- [x] `LICENSE`(MIT) + README long_description. `python -m build` → sdist+wheel, `twine check` PASSED, 새 venv 설치·엔트리포인트 실행 검증. `__version__`은 메타데이터 파생.
- [ ] 실제 업로드: `twine upload dist/*` (유저 PyPI 계정). 그러면 `pip install polyrus-agent` 라이브.

## Phase 1 — 교차검산 + 진실 게이팅 + 보정 측정 + 독립 CLI(graduate)
- [x] `core/arms.py` 병렬 팔(ThreadPoolExecutor) + 콜드스타트 + 탈상관(페르소나·온도) + 코드 추출. **fake 모델로 루프 green 실증.**
- [x] `dod.py` LLM 수용테스트 합성(`DoDGenerator(model=)`) — #2 코어. `models.py` `AnthropicModel`(주입식).
- [x] `polyrus init "<목표>" --llm` — 목표만 주면 LLM이 수용테스트 합성해 task.json 채움(#2 온보딩 마감).
- [x] `t1_mutation.py` 경량 AST 뮤테이션(테스트 강도 게이트, 굿하트) + `t2_differential.py` 독립 재구현 차등(Hypothesis). `full_code_bank()` + 하니스가 콜드 팔을 T2 참조로 배선.
- [x] `eval.py` — ECE/Brier·에스컬레이션 precision/recall·패스율, `score_prb()`. **50 PRB에서 정직성/게이밍 분별 실증.** (v0.5 신뢰성 증명)
- [x] **이해 검증(5.5)** `understanding.py` — 팔의 해석 divergence=모호성 감지, 가정 표면화, 해석/실행 확신 분리, '막는 질문' 대신 회복 가정. 하니스가 생성 *전에* 게이트(모호 시 에스컬레이션).
- [x] **모델 비종속(7.2)** `OpenAIModel`(OpenAI-호환 base_url) + `FallbackModel`(프로바이더 폴백 체인) — 흡수방지의 모델층.
- [x] **발산/창의 모드(4.2)** `divergent.py` — 폭(각도 탈상관)·반클리셰('뻔한 답 거부')·스프레드 보존(단일 수렴 금지, 사람 큐레이션).
- [x] **확신도 보정 루프(5.4)** `calibration.py` — 코퍼스 override → 티어별 경험적 신뢰도 → 하니스가 확신도에 *적용*(자기보고 0.99 대신 보정된 값). 루프 닫힘.
- [x] **가중치 다이얼 A~D + 프리셋(4.3)** `dials.py` — 하나의 정책 객체가 Budget·Config·모드·게이트 파생. 투명한 추천(`recommend`). 프리셋(브레인스토밍/출시/리서치).
- [x] **T4 적대비평(5.4)** `t4_adversarial.py` — Hypothesis 퍼징(견고성, 약 0.4). `full_code_bank`에 등록 → **검증 뱅크 5종 완성**(T1실행·T1뮤테이션·T2차등·T3환각·T4적대).
- [x] **`polyrus run --backend claude|codex`** (owns-loop graduate) — `CliModel`/`claude_cli`/`codex_cli`가 **구독 계정 로그인 그대로** CLI 구동(API 키 불요). Polyrus가 두 모델을 서브루틴으로 몰고 자기 검증 입힘. 모델 비종속의 실물. *주의: 정확한 플래그는 CLI 버전 의존, 구독 자동구동은 ToS·레이트리밋 적용.*
- [ ] (다음) 두 모델 교차검증/토론: Claude 팔 + Codex 팔 → T2 차등·T4 적대로 교차, `coordinator.py` 완성. 그 위에 UI(Next.js+SSE).
- [ ] PRB 홀드아웃 split.

> **v1 기능 마감**: 핵심 아키텍처(수렴+발산 2모드·다이얼·5티어 검증·No-Pass 루프·이해검증·보정 루프·모델비종속·온보딩·Claude Code 웨지·텔레그램·멱등 액션) 완료. 남은 v1: 비-코드 도메인·브라우저(6.2)는 v2(액션/컨텍스트)와 겹쳐 그쪽에서.
>
> **통합/실물 추가**: `session.py`(Dials→전체 조립) · `context.py`→Arms 주입 · **`secrets.py` 실물**(env식 계정 파싱·env/.env/키체인 백엔드·전면 리댁션·CredentialResolver, 텔레그램 연결). `[keychain]`/`[openai]` 선택 의존성.

## Phase 2 — 적대 + 게이트 + 장시간 자율
- [ ] `t4_adversarial.py` 레드팀 + 퍼징.
- [x] `gates.py` 증거조사 게이트(휴먼인루프) — 텔레그램 승인 연결됨.
- [x] `models.py` 멀티 프로바이더 폴백 (OpenAI 호환 + 어댑터) — 위 7.2.
- [x] **자기개선축** `memory.py` `SkillStore` — *검증 통과한 해법만* 스킬로 저장·recall→컨텍스트 주입(Session 닫힌 루프). Hermes(무검증 학습)와 차별: 틀린 걸 학습하지 않음. ngram recall.
- [x] **절제된 자율(자율축, 하트비트 없음)** — 예산 안 끝까지+트레이(기존) + `Session.run(resume=True)` 체크포인트(이미 검증된 항목 건너뜀). 장시간 다단계 재개.
- [ ] 장시간 자율 나머지(6.4): 단계별 저널링(현재 항목 단위 재개까지).
- [x] **브라우저 행동 안전(6.2)** `browser.py` `SafeBrowser` — 읽기우선(자율·교차대조)·쓰기게이팅(fill 자동/click·submit 게이트·멱등·감사·read-back). Playwright 래핑(`[browser]`). 승인자 없으면 안전한 미완료(트레이).

## Phase 3 — 플랫폼
- [ ] 검증 게이트웨이 (OpenAI 호환/MCP)로 기존 에이전트 래핑.
- [ ] 관리형 검증 클라우드 + 감사/거버넌스. **보정 코퍼스 플라이휠** = 진짜 해자(7장).
- [ ] PRB 공개(train) + 홀드아웃 운영, 분기 갱신. 신뢰성을 *증명*으로 마케팅.
- [ ] 검증기 마켓플레이스(양면 네트워크 효과 + 코퍼스 사료).

## 도메인 확장 (해자)
- [x] **재무** `verifiers/finance/` — T1 재계산·단위·대사 + T3 출처대조. `default_finance_bank()`. **같은 골격, 오라클만 교체** 실증(해자가 코드에 안 묶임). + 부수효과로 `AggregateVerdict.passed` 버그 수정(T3 FAIL이 조작 수치를 통과시키던 것 → T1·T2·T3 FAIL 하드 블록, T4만 '우려').
- [x] **검색/RAG** `verifiers/retrieval/` — T1 인용문 축자 대조(날조 차단) + T3 인용 존재(출처 환각) + T3 함의 지지(근거 없는 주장). `default_retrieval_bank()`. **3번째 도메인** = 확장 프레임워크 입증(코드·재무·검색이 같은 골격).
- [ ] 각 도메인 PRB 프로브 패밀리 + 홀드아웃(능력 아니라 정직성 측정).

---
*v0.5 강화(2026-06-18): 해자 재정의(보정 코퍼스/직무분리 증명) · 신뢰성 증명(PRB·ECE) ·
루프 정지정책(No-Silent-Stop·예산 봉투). 상세 → `../20260618_1116_Polyrus_해자·벤치마크·예산정책_강화_기획안.md`, `DESIGN.md` §7–9.*
