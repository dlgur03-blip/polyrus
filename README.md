# Polyrus

검증된 비-패스(No-Pass) 추론 하니스. 최고급 모델 파워유저를 위한 신뢰성 레이어.

> PyPI 배포 이름은 **`polyrus-agent`** (import는 `polyrus`).

## 한 줄
기존 에이전트 루프는 *모델이 "끝"이라고 선언하면* 종료된다.
Polyrus는 **검증기 뱅크가 "완료"라고 판정할 때만** 종료한다.

## 빠른 시작 — 두 줄
```bash
pip install git+https://github.com/dlgur03-blip/polyrus.git
polyrus connect          # Claude/Codex CLI에 자동 연결 (설치된 거 감지)
```
끝. 이제 그 CLI로 코드 작업하면 Polyrus가 '끝' 선언을 **자동 검증**한다 — 미통과면 이어서 고치게.

- **API 키 불요.** 네 `claude`/`codex` 계정 로그인 그대로 사용.
- **연결 메커니즘**: Claude Code = Stop-hook(끝 선언을 가로채 검증) · Codex = owns-loop(`polyrus run "목표" --backend codex`).
- 대상 지정: `polyrus connect claude` / `polyrus connect codex` / `polyrus connect all`. 미리보기 `--dry-run`. 해제 `polyrus unwrap claude`.

## 🤖 Claude Code에게 시키면 자동 설치
터미널의 Claude Code에게:
> "이 깃헙 레포 설치하고 연결해줘: `https://github.com/dlgur03-blip/polyrus`"

→ `pip install git+…` → `polyrus connect` 까지 알아서 함(이 README가 곧 설치 스펙).

## Claude Code에 붙이기 (wrap-first 웨지)
Polyrus는 별도 에이전트가 아니라 **Claude Code를 감싼다**. Claude Code의 `Stop` 훅이
`{"decision":"block","reason":...}`를 반환하면 모델을 못 멈추게 + 이어가게 하는데, 이게
No-Pass 종료조건 역전의 집행점이다.

```bash
polyrus init --wrap      # .polyrus/task.json 골격 생성 + Stop 훅 등록
# .polyrus/task.json 편집: 목표 + 동결 수용 테스트(acceptance_tests)
# 이제 Claude가 "끝" 선언 시 Polyrus가 검증, 미통과면 이어서 고친다.
polyrus status           # 등록/텔레그램 상태
```

작동:
```
Claude "끝" → [polyrus-stop-hook] 검증기 뱅크 대조
   통과   → {} 종료 허용
   미통과 → block + "빠진 항목 X" 재주입 → 호스트가 이어서 수정
   예산소진 → 종료 허용 + 📱 텔레그램 핑(사람 결정)
```

## 텔레그램 알림 + 원탭 승인 (선택, 6.4/6.1)
```bash
export POLYRUS_TELEGRAM_TOKEN=...   export POLYRUS_TELEGRAM_CHAT_ID=...
```
자율 실행 중 막히면 폰으로 핑 + ✅/❌ 버튼으로 승인/거부. 토큰은 코드/로그에 안 들어간다(로컬-우선).

## 검증 티어
- **T1 실행 진실(강)** — ruff + pytest, 결정적 무-LLM 오라클.
- **T3 출처대조(중)** — AST+importlib로 환각 API 차단.
- (T2 교차검산 / T4 적대 / 뮤테이션 — 진행 중.)

## 문서
- `CLAUDE.md` — 아키텍처·컨벤션·시작점
- `DESIGN.md` — 검증기 뱅크·해자·배포 폼팩터(§10)
- `ROADMAP.md` — 단계별 태스크

MIT.
